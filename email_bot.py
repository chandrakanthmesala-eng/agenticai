import sqlite3
import pandas as pd
import time
import os
from openai import OpenAI

# ================= CONFIGURATION =================
# 1. API KEY
GROQ_API_KEY =  
client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=GROQ_API_KEY)

# 2. SETTINGS
DB_PATH = "fraud_detection.db"
CHECK_INTERVAL = 10  # Check every 10 seconds

# ================= HELPER FUNCTIONS =================

def get_db_connection():
    try:
        return sqlite3.connect(DB_PATH, timeout=10)
    except Exception as e:
        print(f"[ERROR] DB Connection: {e}")
        return None

def send_via_outlook(to_email, subject, html_body):
    """
    Attempts to send email using the local Outlook App.
    No passwords or ports required.
    """
    try:
        import win32com.client
        outlook = win32com.client.Dispatch('outlook.application')
        mail = outlook.CreateItem(0)
        mail.To = to_email
        mail.Subject = subject
        mail.HTMLBody = html_body
        mail.Send()
        return True, "Sent via Outlook App"
    except ImportError:
        return False, "pywin32 not installed"
    except Exception as e:
        return False, f"Outlook Error: {e}"

def save_to_file(cust_name, html_body):
    """
    Fallback: Saves the email as an HTML file on the computer.
    """
    filename = f"ALERT_{cust_name}_{int(time.time())}.html"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_body)
    return os.path.abspath(filename)

def mark_as_processed(conn, txn_ids):
    placeholders = ','.join('?' for _ in txn_ids)
    query = f"UPDATE Transactions SET email_sent='YES' WHERE transaction_id IN ({placeholders})"
    conn.execute(query, txn_ids)
    conn.commit()

# ================= MAIN AGENT LOOP =================

def run_agent():
    print("-------------------------------------------------")
    print("🤖 SENTINEL AGENT 2 (CLEAN FORMAT) IS ONLINE")
    print("-------------------------------------------------")
    
    while True:
        conn = get_db_connection()
        if not conn:
            time.sleep(10)
            continue

        try:
            # 1. Fetch Pending Alerts
            query = """
                SELECT t.transaction_id, t.amount, t.currency, t.transaction_date_time, t.transaction_place, t.note,
                       c.customer_id, c.customer_name, c.email_id as cust_email,
                       rm.rm_name
                FROM Transactions t
                JOIN Customer c ON t.customer_id = c.customer_id
                JOIN RelationshipManager rm ON c.rm_id = rm.rm_id
                WHERE (t.transaction_status IN ('On Hold', 'Declined'))
                  AND t.Internal_Flag = 'N'
                  AND (t.email_sent IS NULL OR t.email_sent = 'NO')
            """
            candidates = pd.read_sql(query, conn)

            if candidates.empty:
                print(f"💤 Monitoring... (Next check in {CHECK_INTERVAL}s)")
            else:
                print(f"🚨 Processing {len(candidates)} alerts...")
                
                # 2. Group by Customer
                for cust_id, group in candidates.groupby('customer_id'):
                    first = group.iloc[0]
                    cust_name = first['customer_name']
                    cust_email = first['cust_email']
                    rm_name = first['rm_name']

                    print(f"   > Generating Alert for {cust_name}...")

                    # --- BUILD CLEAN HTML TABLE ---
                    rows = ""
                    txn_ids = []
                    clean_notes = set()

                    for _, row in group.iterrows():
                        txn_ids.append(row['transaction_id'])
                        
                        # CLEAN THE NOTE: Remove [RULE:XXX] if it exists
                        raw_note = str(row['note'])
                        if "]" in raw_note:
                            clean_note = raw_note.split("]")[-1].strip() # Takes part after ']'
                        else:
                            clean_note = raw_note
                        
                        clean_notes.add(clean_note)

                        # Add Row to HTML Table
                        rows += f"""
                        <tr>
                            <td style="padding: 8px;">{row['transaction_date_time']}</td>
                            <td style="padding: 8px;">{row['amount']} {row['currency']}</td>
                            <td style="padding: 8px;">{row['transaction_place']}</td>
                            <td style="padding: 8px;">{clean_note}</td>
                        </tr>
                        """
                    
                    # Complete Table Style
                    table_html = f"""
                    <br>
                    <table border="1" style="border-collapse: collapse; width: 100%; border-color: #ddd; font-family: Arial, sans-serif;">
                        <tr style="background-color: #f2f2f2; text-align: left;">
                            <th style="padding: 10px;">Date</th>
                            <th style="padding: 10px;">Amount</th>
                            <th style="padding: 10px;">Location</th>
                            <th style="padding: 10px;">Issue Detected</th>
                        </tr>
                        {rows}
                    </table>
                    <br>
                    """
                    
                    # --- GENERATE EMAIL INTRO (LLM) ---
                    # We pass the 'clean_notes' to the LLM so it doesn't repeat the [RULE] tags in the body either
                    prompt = f"""
                    You are {rm_name}, a Relationship Manager at Sentinel Bank.
                    Write a short, urgent email body to {cust_name}.
                    Context: We noticed suspicious activity: {', '.join(clean_notes)}.
                    Instruction: 
                    - Keep it professional and urgent.
                    - Ask them to review the table below (I will attach the table).
                    - Ask for a Yes/No reply.
                    - Do NOT output any [RULE] tags.
                    """
                    
                    try:
                        res = client.chat.completions.create(
                            messages=[{"role": "user", "content": prompt}],
                            model="llama-3.3-70b-versatile"
                        )
                        intro_text = res.choices[0].message.content.replace("\n", "<br>")
                        
                        # Combine: Intro + Table + Signature
                        final_html_body = f"""
                        <html>
                        <body style="font-family: Arial, sans-serif; color: #333;">
                            <p>{intro_text}</p>
                            {table_html}
                            <p>Please reply immediately to confirm if these are valid.</p>
                            <p>Best Regards,<br><b>{rm_name}</b><br>Sentinel Bank Security</p>
                        </body>
                        </html>
                        """

                        # --- SEND (Try Outlook, Fallback to File) ---
                        success, msg = send_via_outlook(cust_email, "URGENT: Verify Account Activity", final_html_body)
                        
                        if success:
                            print(f"   ✅ SENT via Outlook to {cust_email}")
                            mark_as_processed(conn, txn_ids)
                        else:
                            print(f"   ⚠️ Outlook failed ({msg}). Switching to DEMO MODE.")
                            file_path = save_to_file(cust_name, final_html_body)
                            print(f"   💾 Email saved to: {file_path}")
                            mark_as_processed(conn, txn_ids)

                    except Exception as e:
                        print(f"   ⚠️ Error: {e}")

        except Exception as e:
            print(f"CRITICAL ERROR: {e}")
        
        finally:
            conn.close()
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run_agent()