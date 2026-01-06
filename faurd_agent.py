import streamlit as st
import sqlite3
import pandas as pd
import json
import re
from openai import OpenAI

# ================= CONFIG =================
st.set_page_config(layout="wide", page_title="Sentinel FRAUD Auditor", page_icon="⚖️")

# API Configuration - Replace with your key
GROQ_API_KEY =  # Ensure this is set
client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)

# Persistent State
if "selected_tid" not in st.session_state: st.session_state.selected_tid = None
if "forensic_report" not in st.session_state: st.session_state.forensic_report = ""

# ================= DATABASE HELPERS =================
def get_db_connection():
    return sqlite3.connect("fraud_detection.db", check_same_thread=False)

def check_schema_update():
    """Ensures the 'note' column exists for saving reasons."""
    conn = get_db_connection()
    try:
        conn.execute("ALTER TABLE Transactions ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass # Column likely already exists
    conn.close()

# Run schema check once on startup
check_schema_update()

def update_txn(tid, status, flag, note=None):
    with get_db_connection() as conn:
        if note:
            conn.execute(
                "UPDATE Transactions SET transaction_status=?, Internal_Flag=?, note=? WHERE transaction_id=?", 
                (status, flag, note, tid)
            )
        else:
            conn.execute(
                "UPDATE Transactions SET transaction_status=?, Internal_Flag=? WHERE transaction_id=?", 
                (status, flag, tid)
            )

def migrate_to_fraud_table(tid, reason, full_report):
    conn = get_db_connection()
    df = pd.read_sql("""
        SELECT t.*, c.customer_name, c.email_id AS cust_email, c.city_name AS customer_home_city,
               rm.rm_name, rm.email_id AS rm_email, rm.phone_number AS rm_phone , c.phone_number AS customer_phone_number
               FROM Transactions t
        JOIN Customer c ON t.customer_id=c.customer_id
        JOIN RelationshipManager rm ON c.rm_id=rm.rm_id
        WHERE t.transaction_id=?
    """, conn, params=(tid,))
    
    if not df.empty:
        r = df.iloc[0].to_dict()
        # Combine the initial Alert Reason + The Full LLM Report
        final_summary = f"ALERT REASON: {reason}\n\nFORENSIC ANALYSIS: {full_report}"
        
        try:
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO fraud_transaction (
                        transaction_id, customer_id, customer_name, customer_email,customer_phone_number, customer_home_city,
                        amount, transaction_place, transaction_date_time, destination_bank_name,
                        rm_name, rm_email, rm_phone, forensic_summary, transaction_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tid, 
                    r.get('customer_id'), 
                    r.get('customer_name'), 
                    r.get('cust_email'),
                    r.get('customer_phone_number'),
                    r.get('customer_home_city'),
                    float(r.get('amount', 0)),
                    r.get('transaction_place'), 
                    str(r.get('transaction_date_time')),
                    r.get('destination_bank_name', 'Unknown'),
                    r.get('rm_name'), 
                    r.get('rm_email'), 
                    r.get('rm_phone'), 
                    final_summary, 
                    "On Hold"
                ))
        except sqlite3.Error as e:
            st.error(f"Database Error during migration: {e}")
            
    conn.close()

# ================= AGENT 1: BACKGROUND AUDITOR =================
@st.fragment(run_every=10)
def background_audit_agent():
    conn = get_db_connection()
    
    # 1. Fetch Pending Records
    pending = pd.read_sql("""
        SELECT t.*, c.city_name, c.Country as home_country 
        FROM Transactions t 
        JOIN Customer c ON t.customer_id=c.customer_id 
        WHERE t.Internal_Flag='N' AND t.transaction_status='Pending'
    """, conn)
    
    # 2. Fetch History
    history = pd.read_sql("""
        SELECT transaction_id, customer_id, transaction_date_time, transaction_place, 
               transaction_country, amount 
        FROM Transactions 
        WHERE Internal_Flag='Y' OR transaction_status IN ('On Hold', 'Declined')
        ORDER BY transaction_date_time DESC LIMIT 50
    """, conn)
    
    if pending.empty:
        conn.close()
        return

    # Updated Prompt with RULE IDs
    prompt = f"""
    Act as a Banking Fraud AI. Analyze PENDING transactions against HISTORY.
    
   STRICT RULES FOR DETECTION:
    
    1. GEO-ANOMALY (Time & Distance Check):
       - COMPARE the current 'customer_id' transaction against the ENTIRE TRANSACTION HISTORY (Status: Pending, On Hold, Approved).
       
       - LOCATION COMPARISON CHECKS:
         a) Compare current 'transaction_place' against Customer Table 'city_name' (Home City) based on customer_id.
         b) Compare current 'transaction_country' against Customer Table 'Country' (Home Country) based on customer_id.
         c) Compare current 'transaction_place' against 'transaction_place' of ALL previous history transactions based on customer_id.
         d) Compare current 'transaction_country' against 'transaction_country' of ALL previous history transactions based on customer_id.
         e) compare current 'transaction_date_time' againt 'transaction_date_time' of ALL previous history transactions based on customer_id.
       - CRITICAL TIME & SAME-SECOND CHECK:
         a) Compare 'customer_id' against history transactions made by the same customer with the EXACT SAME 'transaction_date_time'. 
            If date/time is identical (down to the second) but 'transaction_place' is different -> HOLD.
         b) Different Country: If txn is in a different country than Home or History within <= 36 HOURS -> HOLD.
         c) Same Country: If txn 'transaction_place' is in a different city than Home or History within <= 1 HOURS -> HOLD.
         
    2. SAME-TIME COLLISION (Global & History Check):
       - COMPARE 'customer_id' against their own HISTORY and other PENDING items.
       - IF 'transaction_date_time' is EXACTLY the same (down to the second) but 'transaction_place' is different based on customer_id and transaction_place and transaction_date_time -> HOLD.
       - Logic: "Impossible Simultaneous Travel".
    
    3. VELOCITY (High Frequency):
       - Multiple transactions for same customer within < 60 SECONDS based on customer_id -> HOLD.

    4. STRUCTURING (Smurfing):
       - Amount between 9,000 and 9,999 based on customer_id and "transaction_date_time" and transaction_date_time "check for history transaction_amount of the customer and "approve" if he made same like trsnsaction early" and Transaction amount-> (HOLD,ACCEPT).

    5. PASS-THROUGH (Mule Account):
       - Debit txn occurs < 60 mins after a credit txn of similar value based on customer_id and -> HOLD.

    6. DORMANCY WAKE-UP:
       - No transactions for > 30 days AND current amount > 1000 based on Transaction amount-> HOLD.

    7. MICRO-PROBING:
       - Small txn (< 5.00) followed immediately by large txn (> 500) based on customer_id andtransaction_date_time, amount-> HOLD.

    OUTPUT REQUIREMENTS:
    - Return JSON: {{ "safe": ["ID"], "hold": [{{"id": "ID", "reason": "..."}}] }}
    - **CRITICAL**: Start the 'reason' string with the Rule ID.
      Example: "[RULE:VELOCITY] 4 transactions detected in 10 seconds."
      Example: "[RULE:GEO-ANOMALY] Jump from London to NYC in 15 mins."
    
    PENDING DATA: {pending.to_dict(orient='records')}
    HISTORY DATA: {history.to_dict(orient='records')}
    """
    
    try:
        res = client.chat.completions.create(
            model="llama-3.3-70b-versatile", 
            messages=[
                {"role": "system", "content": "You are a JSON-only detection engine."}, 
                {"role": "user", "content": prompt}
            ], 
            temperature=0
        )
        clean_json = re.search(r"\{.*\}", res.choices[0].message.content, re.DOTALL).group()
        data = json.loads(clean_json)

        for tid in data.get("safe", []):
            update_txn(tid, "Approved", "Y", "Passed Automated Audit")
        for h in data.get("hold", []):
            # Save the reason directly to the DB
            update_txn(h["id"], "On Hold", "N", h["reason"])
            
    except Exception as e:
        print(f"Agent Error: {e}")
    conn.close()

# ================= MAIN UI =================
st.title("🛡️ Sentinel Forensic Dashboard")

# Run Background Agent
background_audit_agent()

# Display On Hold Table
conn = get_db_connection()
# Fetch 'note' as forensic_summary directly from DB
hold_df = pd.read_sql("""
    SELECT transaction_id, customer_id, amount, transaction_place, 
           transaction_date_time, transaction_status, Internal_Flag, 
           note as forensic_summary 
    FROM Transactions 
    WHERE (transaction_status='On Hold' OR transaction_status='Declined') 
    AND Internal_Flag='N'
""", conn)
conn.close()

st.subheader(f"🚨 Anomalies Awaiting Review ({len(hold_df)})")
if not hold_df.empty:
    hold_df['transaction_id'] = hold_df['transaction_id'].astype(str)
    
    event = st.dataframe(
        hold_df, 
        use_container_width=True, 
        hide_index=True, 
        on_select="rerun", 
        selection_mode="single-row", 
        key="main_audit_table"
    )
    
    if event.selection.rows:
        new_tid = hold_df.iloc[event.selection.rows[0]].transaction_id
        if new_tid != st.session_state.selected_tid:
            st.session_state.selected_tid = new_tid
            st.session_state.forensic_report = "" 
            st.rerun()

# ================= DETAIL VIEW =================
if st.session_state.selected_tid:
    tid = st.session_state.selected_tid
    conn = get_db_connection()
    details = pd.read_sql("""
        SELECT t.*, c.customer_name, c.email_id AS cust_email, c.city_name AS home_location, rm.rm_name, rm.email_id AS rm_email 
        FROM Transactions t JOIN Customer c ON t.customer_id=c.customer_id 
        JOIN RelationshipManager rm ON c.rm_id=rm.rm_id WHERE t.transaction_id=?
    """, conn, params=(tid,))
    conn.close()

    if not details.empty:
        r = details.iloc[0]
        st.divider()
        st.subheader(f"🔍 Case Deep-Dive: {tid}")

        with st.expander("📊 Transactional & Stakeholder Context", expanded=True):
            col1, col2 = st.columns(2)
            col1.metric("Amount", f"{r.amount} {r.currency}")
            col1.write(f"**Customer:** {r.customer_name} | **Home City:** {r.home_location}")
            col2.write(f"**Transaction Place:** {r.transaction_place}")
            col2.write(f"**Relationship Manager:** {r.rm_name} ({r.rm_email})")

        # ================= AGENT 2: FORENSIC INVESTIGATOR =================
        with st.expander("🔬 Agent 2: LLM Forensic Investigation", expanded=True):
            if not st.session_state.forensic_report:
                with st.spinner("Analyzing banking rules..."):
                    # Use r.note (from DB) instead of session_state for reliability
                    f_prompt = f"Perform forensic audit for {r.to_dict()}. Detected reason: {r.get('note', 'Unknown')}"
                    f_res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": f_prompt}])
                    st.session_state.forensic_report = f_res.choices[0].message.content
            st.markdown(st.session_state.forensic_report)

        # ================= AGENT 3: CUSTOMER OUTREACH =================
        with st.expander("✉️ Agent 3: Customer Outreach Bot", expanded=True):
            st.info("Agent 3 can draft and simulate sending a verification email to the customer.")
            if st.button("📧 Draft & Send Verification Email", key="agent3_email_btn"):
                with st.spinner("Agent 3 is composing email..."):
                    email_prompt = f"""
                    You are {r.rm_name}, a Relationship Manager at Sentinel Bank.
                    Write a professional, urgent but polite email to your customer, {r.customer_name}.
                    
                    Goal: Ask them to verify a suspicious transaction.
                    
                    Details:
                    - Transaction ID: {tid}
                    - Amount: {r.amount} {r.currency}
                    - Location: {r.transaction_place}
                    - Date: {r.transaction_date_time}
                    
                    Format:
                    From: {r.rm_name} <{r.rm_email}>
                    To: {r.customer_name} <{r.cust_email}>
                    Subject: [URGENT] Verify Activity on your Account
                    Body: [Your drafted text here]
                    """
                    
                    email_res = client.chat.completions.create(
                        model="llama-3.3-70b-versatile", 
                        messages=[{"role": "user", "content": email_prompt}]
                    )
                    email_body = email_res.choices[0].message.content
                    
                    st.success(f"✅ Email successfully sent to {r.cust_email}")
                    st.text_area("Generated Email Log:", value=email_body, height=300)

        # Decision Buttons
        st.divider()
        st.write("### Final Decision")
        btn_app, btn_hold = st.columns(2)
        if btn_app.button("✅ Approve Transaction", use_container_width=True):
            update_txn(tid, "Approved", "Y", "Manual Approval")
            st.session_state.selected_tid = None
            st.rerun()
        
        if btn_hold.button("🟠 Keep On Hold", use_container_width=True):
             update_txn(tid, "On Hold", "Y", r.get('note'))
             st.session_state.selected_tid = None
             st.rerun()
        
      # Fraud Button (Fixed Indentation)
        btn_fraud_col = st.columns(1)[0]
        with btn_fraud_col:
            if st.button("🚨 Confirm Fraud (Finalize Case)", type="primary", use_container_width=True):
                # FIXED: Now passing 3 arguments correctly
                migrate_to_fraud_table(tid, r.get('note', 'Confirmed Fraud'), st.session_state.forensic_report)
                update_txn(tid, "Declined", "Y", "Confirmed Fraud")
                st.session_state.selected_tid = None
                st.toast(f"Case {tid} closed and archived.")
                st.rerun()