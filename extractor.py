import re


def _gender_from_title(title):
    t = title.lower()
    if t.startswith("mstr"):
        return "Male (Child)"
    if t.startswith("mr"):
        return "Male (Adult)"
    if t in ("ms", "mrs"):
        return "Female"
    return "Unknown"


# ---------- Format A: multi-passenger agency ticket (Akbar Travels style) ----------

def _leg_direction(text, pos):
    """Return 'ONWARD' or 'RETURN' depending on which label most recently precedes pos."""
    onward_positions = [m.start() for m in re.finditer(r"\bONWARD\b", text)]
    return_positions = [m.start() for m in re.finditer(r"\bRETURN\b", text)]
    last_onward = max([p for p in onward_positions if p <= pos], default=-1)
    last_return = max([p for p in return_positions if p <= pos], default=-1)
    return "RETURN" if last_return > last_onward else "ONWARD"


def extract_agency_format(text):
    # PNR
    pnr_match = re.search(r"(?:CRS Ref|Airline Ref)\s*:\s*([A-Z0-9]{5,8})", text, re.IGNORECASE)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    # City name -> 3-letter code map, built from anywhere in the doc (order-independent)
    city_to_code = {}
    for m in re.finditer(r"\b([A-Z][a-zA-Z]+)\s*\[([A-Z]{3})\]", text):
        city_to_code[m.group(1)] = m.group(2)

    # Each flight leg has a header line "<CityA> <CityB>" immediately followed by "Airline Ref :"
    # (optionally preceded by ONWARD/RETURN on the same or previous token)
    header_pattern = re.compile(
        r"([A-Z][a-zA-Z]+)\s+([A-Z][a-zA-Z]+)\s*\n(?:Airline Ref)\s*:\s*([A-Z0-9]{5,8})"
    )
    traveler_start = text.find("Traveler(s) Information")
    search_end = traveler_start if traveler_start != -1 else len(text)

    headers = list(header_pattern.finditer(text, 0, search_end))

    onward_legs, return_legs = [], []
    flight_pattern = re.compile(r"([A-Z0-9]{2,4}\s?\d{2,4})\s*\((?:AIRBUS|A\d{3})", re.IGNORECASE)

    for i, hm in enumerate(headers):
        origin_city, dest_city = hm.group(1), hm.group(2)
        origin_code = city_to_code.get(origin_city, "???")
        dest_code = city_to_code.get(dest_city, "???")

        block_end = headers[i + 1].start() if i + 1 < len(headers) else search_end
        block_text = text[hm.end():block_end]
        fm = flight_pattern.search(block_text)
        flight_no = re.sub(r"\s+", " ", fm.group(1)).strip() if fm else "Not Found"

        leg = (flight_no, origin_code, dest_code)
        if _leg_direction(text, hm.start()) == "RETURN":
            return_legs.append(leg)
        else:
            onward_legs.append(leg)

    def chain_sector(legs):
        if not legs:
            return "N/A"
        codes = [origin for _, origin, dest in legs]
        codes.append(legs[-1][2])
        return "-".join(codes)

    def chain_flights(legs):
        return ", ".join(fn for fn, _, _ in legs) if legs else "N/A"

    onward_sector, onward_flight_no = chain_sector(onward_legs), chain_flights(onward_legs)
    return_sector = chain_sector(return_legs) if return_legs else "N/A"
    return_flight_no = chain_flights(return_legs) if return_legs else "N/A"

    # Passengers: title + ALL-CAPS multi-word name (word tokens of 2+ letters, to avoid
    # swallowing the trailing "Nil Nil Nil Nil" columns that follow each row)
    passengers = []
    passenger_pattern = re.compile(r"(Mr|Ms|Mrs|Mstr)\.\s+((?:[A-Z]{2,}\s*)+)")
    seen = set()
    for m in passenger_pattern.finditer(text):
        title, raw_name = m.group(1), m.group(2)
        name = re.sub(r"\s+", " ", raw_name).strip()
        if name == "SWADESHI TRAVELS" or name in seen or not name:
            continue
        seen.add(name)
        passengers.append({
            "PNR": pnr, "Name": name, "Gender": _gender_from_title(title),
            "Sector": onward_sector, "Flight Number": onward_flight_no,
            "Return Sector": return_sector, "Return Flight Number": return_flight_no,
        })
    return passengers


# ---------- Format B: single-passenger-per-page IndiGo boarding pass ----------

def extract_boarding_pass_format(text, ocr_name_lookup=None):
    """
    ocr_name_lookup: optional dict {page_index: "Mr Jaitra Talreja"} supplied by the caller
    when the passenger name cannot be found in the normal text layer (see note below).
    """
    results = []
    pnr_match = re.search(r"\b([A-Z0-9]{6})\s+Confirmed\b", text)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    sector_pattern = re.compile(r"Sector\s+Seat\s+6E Add-ons\s*\n?\s*([A-Z]{3})\s*-\s*([A-Z]{3})")
    flight_pattern = re.compile(
        r"(?:Departing|Returning)\s+Flight\s*[•*]?\s*([A-Z0-9]{2,4}\s?\d{2,5})\s*\(", re.IGNORECASE
    )
    name_pattern = re.compile(r"(Mr|Ms|Mrs|Mstr)\s+([A-Za-z][A-Za-z\s]{2,39}?)\s+Adult", re.IGNORECASE)

    # Split into per-passenger pages using a page-number footer as the anchor,
    # since the name itself is sometimes missing from the text layer (see note).
    page_pattern = re.compile(r"(\d+) of (\d+)")
    page_bounds = [m.start() for m in page_pattern.finditer(text)]
    page_bounds.append(len(text))

    start = 0
    for idx, end in enumerate(page_bounds):
        chunk = text[start:end]
        start = end

        name_match = name_pattern.search(chunk)
        if name_match:
            title, raw_name = name_match.group(1), name_match.group(2)
            name = re.sub(r"\s+", " ", raw_name).strip()
        elif ocr_name_lookup and idx in ocr_name_lookup:
            # ocr_name_lookup[idx] is the full OCR'd text of that page. The layout is always
            # "<Title> <Name words...> <age qualifier: Adult/Child/Infant>\n\nSector ...", so we
            # take everything between the title and the "Sector" keyword (OCR reads plain English
            # words like "Sector" reliably even when it mangles the age qualifier) and drop the
            # last token, which is always that age qualifier - robust to OCR noise on that one
            # word without depending on it being capitalized correctly.
            ocr_page_text = ocr_name_lookup[idx]
            om = re.search(r"(Mr|Ms|Mrs|Mstr)\s+(.*?)\n\s*\n?\s*Sector", ocr_page_text, re.DOTALL)
            if om:
                title = om.group(1)
                words = om.group(2).split()
                name = " ".join(words[:-1]) if len(words) > 1 else " ".join(words)
            else:
                title, name = "Unknown", "Not Found"
        else:
            title, name = "Unknown", "Not Found (name missing from PDF text layer)"

        if "Passenger Information" not in chunk and "Departing Flight" not in chunk:
            continue  # trailing/empty tail chunk after the last real page

        sector_matches = sector_pattern.findall(chunk)
        onward_sector = f"{sector_matches[0][0]}-{sector_matches[0][1]}" if sector_matches else "Not Found"
        return_sector = f"{sector_matches[1][0]}-{sector_matches[1][1]}" if len(sector_matches) > 1 else "N/A"

        flight_matches = flight_pattern.findall(chunk)
        onward_flight_no = re.sub(r"\s+", " ", flight_matches[0]).strip() if flight_matches else "Not Found"
        return_flight_no = re.sub(r"\s+", " ", flight_matches[1]).strip() if len(flight_matches) > 1 else "N/A"

        if not chunk.strip():
            continue
        results.append({
            "PNR": pnr, "Name": name, "Gender": _gender_from_title(title),
            "Sector": onward_sector, "Flight Number": onward_flight_no,
            "Return Sector": return_sector, "Return Flight Number": return_flight_no,
        })
    return results


def extract_ticket_data(text, ocr_name_lookup=None):
    if "Traveler(s) Information" in text:
        return extract_agency_format(text)
    if "Departing Flight" in text or "PNR/Booking Ref" in text:
        return extract_boarding_pass_format(text, ocr_name_lookup=ocr_name_lookup)
    return []
