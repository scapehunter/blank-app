import re
import streamlit as st
import pandas as pd
import pdfplumber

try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

from extractor import extract_ticket_data

st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")
st.title("🎈 CHE Operational Dashboard")
st.write("Upload PDF ticket(s)")


def build_ocr_lookup(pdf, text):
    """
    Only used for the boarding-pass format when the name is missing from the text
    layer entirely. OCRs every page and returns {page_index: full_ocr_text}.
    """
    if not OCR_AVAILABLE:
        return None
    if "Departing Flight" not in text:
        return None
    if re.search(r"(Mr|Ms|Mrs|Mstr)\s+[A-Za-z][A-Za-z\s]{2,39}?\s+Adult", text, re.IGNORECASE):
        return None  # name already present in text layer, no OCR needed

    lookup = {}
    for i, page in enumerate(pdf.pages):
        try:
            image = page.to_image(resolution=200).original
            lookup[i] = pytesseract.image_to_string(image)
        except Exception:
            continue
    return lookup


def extract_from_pdf(uploaded_file):
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            ocr_lookup = build_ocr_lookup(pdf, text)
    except Exception as e:
        st.error(f"Error reading {uploaded_file.name}: {e}")
        return None

    rows = extract_ticket_data(text, ocr_name_lookup=ocr_lookup)
    if not rows:
        return None

    for row in rows:
        row["File Name"] = uploaded_file.name

    return rows


uploaded_files = st.file_uploader("Upload Ticket(s)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    all_rows = []
    with st.spinner("Extracting passenger data from PDFs..."):
        for f in uploaded_files:
            rows = extract_from_pdf(f)
            if rows:
                all_rows.extend(rows)
            else:
                st.warning(f"Could not extract any passenger rows from {f.name}.")

    if not all_rows:
        st.error("Error: Could not extract valid data from the uploaded PDF(s). "
                  "Ensure text is selectable (not a scanned image).")
    else:
        st.success(f"Extracted {len(all_rows)} passenger row(s) from {len(uploaded_files)} file(s).")
        column_order = ["File Name", "PNR", "Name", "Gender", "Sector",
                         "Flight Number", "Return Sector", "Return Flight Number"]
        df = pd.DataFrame(all_rows)[column_order]
        st.dataframe(df, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download as CSV", csv, "ticket_data.csv", "text/csv")