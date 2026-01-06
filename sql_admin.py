import streamlit as st
import sqlite3
import pandas as pd
from openai import OpenAI  
import time

# --- 1. SETUP & CONFIG ---
# Replace with your actual key or use st.secrets
GROQ_API_KEY =  # Ensure this is set
client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)

st.set_page_config(layout="wide", page_title="Sentinel SQL Admin", page_icon="🛡️")

# --- 2. DATABASE INITIALIZATION ---
def init_db():
    conn = sqlite3.connect('fraud_detection.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.executescript('''
        PRAGMA foreign_keys = ON;
        
        CREATE TABLE IF NOT EXISTS RelationshipManager (
            rm_id INTEGER PRIMARY KEY AUTOINCREMENT, 
            rm_name TEXT NOT NULL, phone_number TEXT NOT NULL, email_id TEXT NOT NULL);
        
        CREATE TABLE IF NOT EXISTS Customer (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT, 
            customer_name TEXT NOT NULL, customer_account INTEGER NOT NULL, city_name TEXT NOT NULL, postal_code TEXT, 
            phone_number TEXT NOT NULL, email_id TEXT NOT NULL, ssn_number TEXT NOT NULL, 
            rm_id INTEGER NOT NULL, FOREIGN KEY (rm_id) REFERENCES RelationshipManager(rm_id));
            
        CREATE TABLE IF NOT EXISTS Transactions (
            transaction_id TEXT PRIMARY KEY, 
            customer_id INTEGER NOT NULL, 
            transaction_date_time TEXT NOT NULL, 
            transaction_place TEXT NOT NULL, 
            transaction_category TEXT NOT NULL, 
            transaction_type TEXT NOT NULL,
            source_account_id INTEGER,
            destination_account_id INTEGER, 
            destination_bank_name TEXT,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            transaction_status TEXT DEFAULT 'Pending',
            Internal_Flag TEXT DEFAULT 'N',
            transaction_country TEXT NOT NULL,
            note TEXT,
            email_sent TEXT DEFAULT 'NO',
            FOREIGN KEY (customer_id) REFERENCES Customer(customer_id));

            CREATE TABLE IF NOT EXISTS fraud_transaction (
            transaction_id TEXT NOT NULL, 
            customer_id INTEGER NOT NULL,
            customer_name TEXT NOT NULL,
            customer_email TEXT NOT NULL,
            customer_phone_number TEXT NOT NULL,
            customer_home_city TEXT NOT NULL, 
            rm_name TEXT NOT NULL,
            rm_email TEXT NOT NULL,
            rm_phone TEXT NOT NULL,
            transaction_date_time TEXT NOT NULL, 
            transaction_place TEXT NOT NULL,
            destination_bank_name TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            transaction_status TEXT NOT NULL,
            status TEXT DEFAULT 'N',
            forensic_summary TEXT NOT NULL);
	''')
    
    # Initialize sequences if empty
    cursor.execute("SELECT count(*) FROM sqlite_sequence")
    if cursor.fetchone()[0] == 0: 
        cursor.executescript('''
            INSERT OR IGNORE INTO sqlite_sequence (name, seq) VALUES ('RelationshipManager', 9999999);
            INSERT OR IGNORE INTO sqlite_sequence (name, seq) VALUES ('Customer', 99999);
        ''')
    conn.commit()
    return conn

conn = init_db()

# --- 3. SIDEBAR (SYSTEM STYLE) ---
with st.sidebar:
    st.title("🛡️ Admin Control")
    st.info("System Status: Online", icon="✅")
    
    if st.button("🧹 Clear Chat"):
        st.session_state.messages = []
        st.rerun()

    st.divider()
    if "messages" not in st.session_state: st.session_state.messages = []
    
    chat_container = st.container(height=400)
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])

    if prompt := st.chat_input("Command Sentinel..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        # Context fetching for the LLM
        cust_context = pd.read_sql("SELECT customer_id, customer_account FROM Customer", conn).to_string(index=False)

        # UPDATED SCHEMA INSTRUCTION BELOW
        sys_instr = f"""You are the Sentinel SQL Architect. 
        OPERATE ONLY WITHIN THIS SCHEMA:
        - RelationshipManager: rm_id (PK), rm_name, phone_number, email_id
        - Customer: customer_id (PK), customer_name, customer_account, city_name, postal_code, phone_number, email_id, ssn_number, rm_id (FK)
        - Transactions: transaction_id (TEXT PK), customer_id (FK), transaction_date_time, transaction_place, transaction_category, transaction_type, source_account_id, destination_account_id, destination_bank_name, amount, currency, transaction_country.

        CRITICAL BUSINESS RULES:
        1. SYNTAX: Use strictly SQLite syntax. 
           - NO `UNHEX()`, NO `UUID()`, NO `NOW()`.
           - Use `DATETIME('now')` for current time.
           - For `transaction_id`, generate a random text string with 16 characters (e.g., 'TX-0000000000000000') or use `HEX(RANDOMBLOB(16))`.
        2. SOURCE ACCOUNT: When inserting a transaction for a customer_id, use their 'customer_account' from Customer table as 'source_account_id'.
        3. DATA CONTEXT: {cust_context}

        OUTPUT FORMAT: SQL only in ```sql blocks."""
        
        try:
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile", 
                messages=[{"role": "system", "content": sys_instr}, {"role": "user", "content": prompt}], 
                temperature=0
            )
            ai_resp = completion.choices[0].message.content
            st.session_state.messages.append({"role": "assistant", "content": ai_resp})
            
            if "```sql" in ai_resp:
                st.session_state.pending_sql = ai_resp.split("```sql")[1].split("```")[0].strip()
            st.rerun()
        except Exception as e:
            st.error(f"LLM Error: {e}")

# --- 4. MAIN DASHBOARD ---
st.title("🛡️ Sentinel Command Dashboard")

# Metrics (Updated to 3 columns)
m1, m2, m3 = st.columns(3)
with m1: st.metric("RMs", pd.read_sql("SELECT COUNT(*) FROM RelationshipManager", conn).iloc[0,0])
with m2: st.metric("Customers", pd.read_sql("SELECT COUNT(*) FROM Customer", conn).iloc[0,0])
with m3: st.metric("Transactions", pd.read_sql("SELECT COUNT(*) FROM Transactions", conn).iloc[0,0])

# SQL Preview
if "pending_sql" in st.session_state:
    st.subheader("⚡ SQL Preview")
    final_sql = st.text_area("Execution Script:", value=st.session_state.pending_sql, height=150)
    if st.button("▶️ Run Script", type="primary"):
        try:
            conn.cursor().executescript(final_sql)
            conn.commit()
            st.success("Execution Successful")
            del st.session_state.pending_sql
            st.rerun()
        except Exception as e: st.error(f"SQL Error: {e}")

# Table Explorer (Updated Tabs)
st.subheader("📂 Table Dashboard")
tabs = st.tabs(["Customer", "Transactions", "RelationshipManager", "Fraud Transactions"])
with tabs[0]: st.dataframe(pd.read_sql("SELECT * FROM Customer", conn), use_container_width=True)
with tabs[1]: st.dataframe(pd.read_sql("SELECT * FROM Transactions", conn), use_container_width=True)
with tabs[2]: st.dataframe(pd.read_sql("SELECT * FROM RelationshipManager", conn), use_container_width=True)
with tabs[3]: st.dataframe(pd.read_sql("SELECT * FROM fraud_transaction", conn), use_container_width=True)