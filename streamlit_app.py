import io
import re
import unicodedata

import pandas as pd
import streamlit as st
from pypdf import PdfReader

# ============================================================================
# Ticket extraction logic (verified against real uploaded PDFs — see notes
# inline for the specific real-text quirks each fix addresses)
# ============================================================================

TITLE_GENDER = {
    "MR": "Male", "MSTR": "Male", "MASTER": "Male",
    "MS": "Female", "MRS": "Female", "MISS": "Female",
}

NOT_IDENTIFIED = "Not Identified"


def _normalize(text):
    """Real PDF text extraction (pypdf/pdfplumber) can contain non-breaking
    spaces (\\xa0) and other unicode whitespace variants in place of plain
    spaces — e.g. 'Airline\\xa0Ref' instead of 'Airline Ref'. Substring
    checks and literal-space regexes silently fail to match these unless
    the text is normalized first."""
    text = text.replace("\xa0", " ")
    text = unicodedata.normalize("NFKC", text)
    return text


def _gender(title):
    return TITLE_GENDER.get(title.upper().rstrip("."), None)


def _fill(value):
    """Replace missing/empty values with an explicit marker instead of
    silently dropping the field — every column must always have something
    visible, never a blank or omitted entry."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return NOT_IDENTIFIED
    return value


# ---------------------------------------------------------------------------
# Format A: Agency multi-passenger table (Akbar Travels / Swadeshi Travels
# style). Markers: "ONWARD"/"RETURN" section headers + "Airline Ref :" PNR.
# ---------------------------------------------------------------------------
def _is_format_a(text):
    return "Airline Ref" in text and ("ONWARD" in text or "RETURN" in text)


def _extract_format_a(text):
    legs = []
    leg_pattern = re.compile(
        r"(ONWARD|RETURN)\s+(.*?)\n.*?Airline Ref\s*:\s*([A-Z0-9]{5,6})",
        re.DOTALL,
    )
    header_spans = [(m.start(), m.group(1), m.group(2).strip(), m.group(3))
                     for m in leg_pattern.finditer(text)]
    trav_idx = text.find("Traveler(s) Information")
    bounds = [h[0] for h in header_spans] + [trav_idx if trav_idx != -1 else len(text)]

    for i, (start, direction, header_route, pnr) in enumerate(header_spans):
        block = text[start:bounds[i + 1]]
        # Flight number sits right after the "...Arrival date & time" table
        # header and before the first "[XXX]" IATA code — its exact line
        # breaks vary between legs in real extracted text, so anchor on
        # word boundaries within that window instead of surrounding newlines.
        header_idx = block.find("Arrival date & time")
        bracket_idx = block.find("[", header_idx if header_idx != -1 else 0)
        window = block[header_idx:bracket_idx] if header_idx != -1 and bracket_idx != -1 else block
        fn_match = re.search(r"\b([A-Z0-9]{1,3}\s?\d{3,4})\b", window)
        flight_number = fn_match.group(1).strip() if fn_match else None
        codes = re.findall(r"([A-Za-z][A-Za-z\s]*?)\s*\[([A-Z]{3})\]", block)
        origin = codes[0] if len(codes) > 0 else (None, None)
        dest = codes[1] if len(codes) > 1 else (None, None)
        legs.append({
            "direction": direction, "pnr": pnr, "flight_number": flight_number,
            "sector": f"{origin[1]}-{dest[1]}" if origin[1] and dest[1] else None,
        })

    trav_block = text[trav_idx:] if trav_idx != -1 else text
    name_pattern = re.compile(
        r"\b(Mr\.|Ms\.|Mrs\.|Miss|Master)[ \t]+((?:(?!Nil\b)[A-Z][A-Za-z]*[ \t]*)+)"
    )
    seen = {}
    for m in name_pattern.finditer(trav_block):
        title, name = m.group(1), m.group(2).strip()
        # Real PDF text sometimes glues the row's "Nil" values directly onto
        # the name with no space (e.g. "LITTMANNNil") — strip that suffix.
        name = re.sub(r"Nil$", "", name).strip()
        key = name.upper()
        if key not in seen:
            seen[key] = {"name": name, "gender": _gender(title.rstrip("."))}
    return {"booking_ref": legs[0]["pnr"] if legs else None,
            "legs": legs, "passengers": list(seen.values())}


# ---------------------------------------------------------------------------
# Format B: IndiGo single-passenger-per-page itinerary. Markers:
# "Departing Flight" / "Return Flight" headers. NOTE: verified against a
# real uploaded file that pypdf/pdfplumber both drop the "PNR/Booking Ref."
# label AND the passenger name entirely from the text layer (a rendering
# artifact in the source PDF, not an extraction bug) — the PNR is recovered
# via a fallback pattern, but the name genuinely cannot be recovered from
# text extraction and is reported as "Not Identified" rather than guessed.
# ---------------------------------------------------------------------------
def _is_format_b(text):
    return "Departing Flight" in text


def _extract_format_b(text):
    pnr = None
    pnr_match = re.search(r"PNR/Booking Ref\.?\s*\n?\s*([A-Z0-9]{5,6})", text)
    if pnr_match:
        pnr = pnr_match.group(1)
    else:
        fallback_match = re.search(r"\b([A-Z0-9]{5,7})Confirmed", text)
        if fallback_match:
            pnr = fallback_match.group(1)

    sectors = re.findall(r"([A-Z]{3}-[A-Z]{3})", text)

    leg_pattern = re.compile(
        r"(Departing Flight|Return Flight)\s+([A-Z0-9]{1,3}\s?\d{3,5})\s*\(([A-Z0-9]+)\)"
        r"\s*([\d]{1,2}\s\w+\s\d{4})"
    )
    legs = []
    for i, m in enumerate(leg_pattern.finditer(text)):
        direction, flight_number, aircraft, date = m.groups()
        sector = sectors[i] if i < len(sectors) else None
        legs.append({
            "direction": "ONWARD" if "Departing" in direction else "RETURN",
            "pnr": pnr, "flight_number": flight_number.strip(), "sector": sector,
        })

    name_match = re.search(
        r"\b((?i:Mr|Ms|Mrs|Miss|Master))\.?\s+([A-Z][A-Za-z]*"
        r"(?:\s+(?!Adult\b|Sector\b|Seat\b|Male\b|Female\b)[A-Z][A-Za-z]*)*)",
        text,
    )
    passengers = []
    if name_match:
        title, name = name_match.groups()
        passengers.append({"name": name.strip(), "gender": _gender(title)})

    return {"booking_ref": pnr, "legs": legs, "passengers": passengers}


# ---------------------------------------------------------------------------
# Format C: Consolidator/agency e-ticket with a different Airline PNR per
# leg (e.g. TripJack-style Akasa booking). Markers: "Booking ID:" + "Airline
# PNR" label.
# ---------------------------------------------------------------------------
def _is_format_c(text):
    return "Airline PNR" in text and "Booking ID" in text


def _extract_format_c(text):
    booking_ref_match = re.search(r"Booking ID:\s*(\S+)", text)
    booking_ref = booking_ref_match.group(1) if booking_ref_match else None

    leg_pnrs = re.findall(r"([A-Z0-9]{5,6})\s*\nAirline PNR", text)

    blocks = re.split(r"(?=Flight Detail)", text)
    flight_blocks = [b for b in blocks if b.startswith("Flight Detail")]

    legs = []
    for i, block in enumerate(flight_blocks):
        fn_match = re.search(r"\b([A-Z]{1,2}\s*-\s*\d{3,5})\b", block)
        flight_number = fn_match.group(1).replace(" ", "") if fn_match else None
        sector_match = re.search(r"\b([A-Z]{3})-([A-Z]{3})\b", block)
        origin, dest = (sector_match.groups() if sector_match else (None, None))
        pnr = leg_pnrs[i] if i < len(leg_pnrs) else None
        legs.append({
            "direction": "ONWARD" if i == 0 else "RETURN",
            "pnr": pnr, "flight_number": flight_number,
            "sector": f"{origin}-{dest}" if origin and dest else None,
        })

    name_pattern = re.compile(
        r"\b(MS|MR|MRS|MISS|MSTR)\s+([A-Z][A-Z\s]+?)\s*\(\s*[A-Z]\s*\)"
    )
    seen = {}
    for m in name_pattern.finditer(text):
        title, name = m.group(1), m.group(2).strip()
        key = name.upper()
        if key not in seen:
            seen[key] = {"name": name, "gender": _gender(title)}

    return {"booking_ref": booking_ref, "legs": legs, "passengers": list(seen.values())}


# ---------------------------------------------------------------------------
# Format D: goindigo.in web itinerary (distinct from the IndiGo per-passenger
# PDF in Format B — this one comes from the goindigo.in "Itinerary" web page,
# uses "PNR/Booking Reference" with no period, states gender inline as
# "Adult | Male |", gives sectors directly (e.g. "IXE-BOM"), and can have
# multiple legs that are NOT a round trip (e.g. IXE-BOM-DEL is a connecting
# multi-city itinerary, not "onward/return") — so legs are labeled
# sequentially ("LEG 1", "LEG 2") rather than guessed as onward/return.
# ---------------------------------------------------------------------------
def _is_format_d(text):
    return "PNR/Booking Reference" in text


def _extract_format_d(text):
    pnr_match = re.search(r"PNR/Booking Reference\s*([A-Z0-9]{5,7})", text)
    pnr = pnr_match.group(1) if pnr_match else None

    name_match = re.search(
        r"(Mr|Ms|Mrs|Miss|Master)\.?\s+([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*)\s*Adult",
        text,
    )
    passengers = []
    gender = None
    gender_match = re.search(r"Adult\s*\|\s*(Male|Female)\s*\|", text)
    if gender_match:
        gender = gender_match.group(1)
    if name_match:
        title, name = name_match.groups()
        passengers.append({"name": name.strip(), "gender": gender or _gender(title)})

    sectors = re.findall(r"\b([A-Z]{3})\s*-\s*([A-Z]{3})\b", text)
    flight_pattern = re.compile(
        r"([A-Z0-9]{1,3}\s?\d{3,5})\s*\(([A-Z0-9]+)\)\s*(\d{1,2}\s\S+\s\d{4})Check-in"
    )
    flights = flight_pattern.findall(text)

    legs = []
    n = max(len(sectors), len(flights))
    for i in range(n):
        origin, dest = sectors[i] if i < len(sectors) else (None, None)
        flight_number = flights[i][0].strip() if i < len(flights) else None
        legs.append({
            "direction": f"LEG {i + 1}", "pnr": pnr,
            "flight_number": flight_number,
            "sector": f"{origin}-{dest}" if origin and dest else None,
        })

    return {"booking_ref": pnr, "legs": legs, "passengers": passengers}


# ---------------------------------------------------------------------------
# Format E: Air India Express itinerary. Markers: "Air India Express" +
# "PNR :" label. Verified against one real sample — single leg, direction
# given explicitly as "Onward"/"Return" in the text.
# ---------------------------------------------------------------------------
def _is_format_e(text):
    return "Air India Express" in text


def _extract_format_e(text):
    pnr_match = re.search(r"PNR\s*:\s*([A-Z0-9]{5,7})", text)
    pnr = pnr_match.group(1) if pnr_match else None

    name_match = re.search(
        r"Name\s*Seat\s*Add\s*Ons\s*\n?\s*(Mr|Ms|Mrs|Miss|Master)\.?[ \t]+([A-Za-z]+(?:[ \t]+[A-Za-z]+)*)",
        text,
    )
    passengers = []
    if name_match:
        title, name = name_match.groups()
        passengers.append({"name": name.strip(), "gender": _gender(title)})

    sector_match = re.search(r"\b([A-Z]{3})\s*-\s*([A-Z]{3})\b", text)
    origin, dest = (sector_match.groups() if sector_match else (None, None))

    flight_match = re.search(
        r"\b([A-Z]{1,2}\s?\d{3,5})(?=(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),)", text
    )
    flight_number = flight_match.group(1).strip() if flight_match else None

    direction_match = re.search(r"\b(Onward|Return)\b", text)
    direction = direction_match.group(1).upper() if direction_match else None

    legs = [{
        "direction": direction, "pnr": pnr, "flight_number": flight_number,
        "sector": f"{origin}-{dest}" if origin and dest else None,
    }]

    return {"booking_ref": pnr, "legs": legs, "passengers": passengers}


FORMATS = [
    ("Agency multi-passenger (ONWARD/RETURN)", _is_format_a, _extract_format_a),
    ("IndiGo per-passenger itinerary", _is_format_b, _extract_format_b),
    ("Agency e-ticket, per-leg PNR", _is_format_c, _extract_format_c),
    ("goindigo.in web itinerary", _is_format_d, _extract_format_d),
    ("Air India Express itinerary", _is_format_e, _extract_format_e),
]


def extract_ticket(text):
    text = _normalize(text)
    for name, detector, extractor in FORMATS:
        if detector(text):
            result = extractor(text)
            result["_format"] = name
            return result
    return {"_format": "Unrecognized format", "booking_ref": None, "legs": [], "passengers": []}


def _has_missing_fields(result):
    """True if any field a real ticket page should have is still missing
    after text-layer extraction — this is what triggers the OCR fallback,
    rather than running OCR on every page unconditionally."""
    if not result.get("passengers"):
        return True
    if not result.get("booking_ref"):
        return True
    legs = result.get("legs", [])
    if not legs:
        return True
    for leg in legs:
        if not leg.get("pnr") or not leg.get("sector") or not leg.get("flight_number"):
            return True
    return False


def _merge_ocr_result(text_result, ocr_result):
    """Fill in ONLY the fields text-layer extraction missed, using what OCR
    found. Never overwrites a field that was already successfully
    extracted from the text layer."""
    merged = dict(text_result)
    merged["legs"] = [dict(l) for l in text_result.get("legs", [])]

    if not merged.get("booking_ref") and ocr_result.get("booking_ref"):
        merged["booking_ref"] = ocr_result["booking_ref"]

    if not merged.get("passengers") and ocr_result.get("passengers"):
        merged["passengers"] = ocr_result["passengers"]

    ocr_legs = ocr_result.get("legs", [])
    if not merged["legs"] and ocr_legs:
        merged["legs"] = ocr_legs
    else:
        for i, leg in enumerate(merged["legs"]):
            ocr_leg = next((ol for ol in ocr_legs if ol.get("direction") == leg.get("direction")), None)
            if ocr_leg is None and i < len(ocr_legs):
                ocr_leg = ocr_legs[i]
            if ocr_leg:
                for field in ("pnr", "sector", "flight_number"):
                    if not leg.get(field) and ocr_leg.get(field):
                        leg[field] = ocr_leg[field]

    if merged.get("_format") == "Unrecognized format" and ocr_result.get("_format") != "Unrecognized format":
        merged["_format"] = ocr_result.get("_format")

    return merged


def ocr_page_text(pdf_bytes, page_num):
    """Render one page to an image and OCR it. Used only as a fallback when
    the text layer is missing something — verified against a real ticket
    where the passenger name is present visually but dropped from the text
    layer by a PDF-rendering artifact."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
        images = convert_from_bytes(pdf_bytes, first_page=page_num, last_page=page_num, dpi=200)
        if not images:
            return ""
        return pytesseract.image_to_string(images[0])
    except Exception:
        return ""


def to_rows(result, source_file, page_num):
    """One row per (passenger, leg). Every field is guaranteed to be
    present: missing values are marked 'Not Identified' rather than
    silently dropped, so a failed extraction is always visible."""
    rows = []
    legs = result.get("legs", [])
    passengers = result.get("passengers", [])
    if not passengers:
        passengers = [{"name": None, "gender": None}]
    if not legs:
        legs = [{"direction": None, "pnr": result.get("booking_ref"), "flight_number": None, "sector": None}]

    for pax in passengers:
        for leg in legs:
            rows.append({
                "Source File": source_file,
                "Page": page_num,
                "Format Detected": result.get("_format"),
                "Name": _fill(pax.get("name")),
                "Gender": _fill(pax.get("gender")),
                "PNR": _fill(leg.get("pnr")),
                "Direction": _fill(leg.get("direction")),
                "Sector": _fill(leg.get("sector")),
                "Flight Number": _fill(leg.get("flight_number")),
                "Booking Ref": _fill(result.get("booking_ref")),
            })
    return rows


# ============================================================================
# Streamlit app
# ============================================================================

st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")
st.title("🎈 CHE Operational Dashboard")
st.write("Upload one or more PDF tickets to extract passenger, PNR, sector, and flight details.")

uploaded_files = st.file_uploader(
    "Upload Ticket(s)", type=["pdf"], accept_multiple_files=True
)

if uploaded_files:
    all_rows = []
    for pdf_file in uploaded_files:
        with st.spinner(f"Extracting data from {pdf_file.name}..."):
            pdf_bytes = pdf_file.getvalue()
            try:
                reader = PdfReader(io.BytesIO(pdf_bytes))
            except Exception as e:
                st.error(f"Could not open {pdf_file.name}: {e}")
                continue

            # Each page is processed independently — no merging across
            # pages, since some formats repeat the same booking once per
            # passenger page (e.g. the IndiGo itinerary format).
            for page_num, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if not text.strip():
                    all_rows.append({
                        "Source File": pdf_file.name, "Page": page_num,
                        "Format Detected": "No extractable text",
                        "Name": NOT_IDENTIFIED, "Gender": NOT_IDENTIFIED,
                        "PNR": NOT_IDENTIFIED, "Direction": NOT_IDENTIFIED,
                        "Sector": NOT_IDENTIFIED, "Flight Number": NOT_IDENTIFIED,
                        "Booking Ref": NOT_IDENTIFIED,
                    })
                    continue

                result = extract_ticket(text)

                # OCR fallback: only runs when text-layer extraction left
                # something missing, and only fills in what OCR actually
                # finds — anything still missing after that stays
                # "Not Identified" rather than being guessed.
                if _has_missing_fields(result):
                    ocr_text = ocr_page_text(pdf_bytes, page_num)
                    if ocr_text.strip():
                        ocr_result = extract_ticket(ocr_text)
                        result = _merge_ocr_result(result, ocr_result)
                        if _has_missing_fields(result):
                            result["_format"] = f"{result.get('_format')} (OCR fallback used, still partial)"
                        else:
                            result["_format"] = f"{result.get('_format')} (OCR fallback used)"

                all_rows.extend(to_rows(result, pdf_file.name, page_num))

    if all_rows:
        df = pd.DataFrame(all_rows)
        st.success(f"Extracted {len(df)} row(s) from {len(uploaded_files)} file(s).")
        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download as CSV", data=csv,
            file_name="ticket_extraction_results.csv", mime="text/csv",
        )
    else:
        st.warning("No data could be extracted from the uploaded file(s).")
else:
    st.info("Upload one or more ticket PDFs to get started.")