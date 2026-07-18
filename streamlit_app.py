import streamlit as st
import pandas as pd
from pypdf import PdfReader
import glob


st.set_page_config(page_title="CHE Operational Dashboard", layout="wide")

st.title("🎈 CHE Operational Dashboard")
st.write("Upload 1 PDF ticket")

col = st.columns(1)
with col:
    pdf_file = st.file_uploader("Upload Ticket", type=["pdf"])

if pdf_file:
    with st.spinner("Extracting tabular matrices from PDFs..."):
        df = extractDetailsfromPdfTicket(pdf_file)

if df is None:
    st.error("Error: Could not extract valid structural tables from one or both PDFs. Ensure text is selectable (not scanned images).")
else:

    st.success("Tabular contents extracted successfully from both files!")


def extractDetailsfromPdfTicket(uploaded_ticket):
    all_tickets = []
    print(f"\n--- Reading: {uploaded_ticket} ---")
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
            "File Name": uploaded_ticket,
            "PNR": ticket["pnr"],
            "Name": passengerTicket["name"],
            "Gender": passengerTicket["gender"],
            })
        print("Successfully processed: {pdf_path}")
    except Exception as e:
        print(f"Error reading {uploaded_ticket}: {e}")

    headers = [str(h).strip() if h else f"Column_{i}" for i, h in enumerate(all_tickets[0])]
    df = pd.DataFrame(all_tickets[1:], columns=headers)
    return df


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
            # We look back at the original text to grab the title prefix for this specific name
            prefix_match = re.search(r"(Mr\.|Ms\.|Mrs\.|Mstr\.)\s+" + re.escape(name), text, re.IGNORECASE)
            title = prefix_match.group(1).lower() if prefix_match else ""

            gender = "Unknown"
            if "mr." in title:
                gender = "Male (Adult)"
            elif "mstr." in title:
                gender = "Male (Child)"
            elif "ms." in title or "mrs." in title:
                gender = "Female"
            else:
              print("gender error")
              gender = "error"

            passengers.append({
                "name": clean_name,
                "gender": gender
            })

    return {
        "pnr": pnr,
        "passengers": passengers
    }
