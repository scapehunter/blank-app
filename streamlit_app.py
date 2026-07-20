import streamlit as st
import pandas as pd
from pypdf import PdfReader
import re

st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")

st.title("🎈 CHE Operational Dashboard")
st.write("Upload 1 PDF ticket")


def extract_ticket_data(text):
    # 1. Regex for PNR (Matches 'Airline Ref' or 'CRS Ref' followed by a 6-character code)
    pnr_pattern = r"(?:Airline Ref|CRS Ref)\s*:\s*([A-Z0-9]{6})"
    pnr_match = re.search(pnr_pattern, text, re.IGNORECASE)
    pnr = pnr_match.group(1).upper() if pnr_match else "Not Found"

    # 2. Regex for Passengers (Matches titles with dots like Mr. or Ms. and captures the name)
    passenger_pattern = r"(?:Mr\.|Ms\.|Mrs\.|Mstr\.)\s+([A-Z\s]{3,40})"

    # Find all matches for passenger strings
    raw_matches = re.findall(passenger_pattern, text, re.IGNORECASE)

    passengers = []
    for name in raw_matches:
        # Clean up any residual structural characters left over from the ticket table layout
        clean_name = re.sub(r'[\r\n\t",]', '', name).strip()

        # Deduplicate and skip corporate titles if they accidentally match
        if clean_name and clean_name != "SWADESHI TRAVELS" and clean_name not in [p['name'] for p in passengers]:
            # Determine gender based on the prefix mapping in the original text chunk
            prefix_match = re.search(r"(Mr\.|Ms\.|Mrs\.|Mstr\.)\s+" + re.escape(name), text, re.IGNORECASE)
            title = prefix_match.group(1).lower() if prefix_match else ""

            if "mr." in title:
                gender = "Male (Adult)"
            elif "mstr." in title:
                gender = "Male (Child)"
            elif "ms." in title or "mrs." in title:
                gender = "Female"
            else:
                gender = "Unknown"

            passengers.append({
                "name": clean_name,
                "gender": gender
            })

    return {
        "pnr": pnr,
        "passengers": passengers
    }


def extractDetailsfromPdfTicket(uploaded_ticket):
    all_tickets = []
    try:
        reader = PdfReader(uploaded_ticket)
        full_text = ""

        # Combine text from all pages in the PDF
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"

        ticket = extract_ticket_data(full_text)
        # Append the extracted data as a dictionary
        for passengerTicket in ticket["passengers"]:
            all_tickets.append({
                "File Name": uploaded_ticket.name,
                "PNR": ticket["pnr"],
                "Name": passengerTicket["name"],
                "Gender": passengerTicket["gender"],
            })
    except Exception as e:
        st.error(f"Error reading {uploaded_ticket.name}: {e}")
        return None

    if not all_tickets:
        return None

    return pd.DataFrame(all_tickets)


pdf_file = st.file_uploader("Upload Ticket", type=["pdf"])

if pdf_file:
    with st.spinner("Extracting tabular matrices from PDFs..."):
        df = extractDetailsfromPdfTicket(pdf_file)

    if df is None:
        st.error("Error: Could not extract valid structural tables from the PDF. Ensure text is selectable (not scanned images).")
    else:
        st.success("Tabular contents extracted successfully!")
        st.dataframe(df)