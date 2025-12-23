import sqlite3
from faker import Faker
import random
from datetime import datetime, timedelta

# Initialize Faker
fake = Faker()

# Connect to SQLite database
conn = sqlite3.connect('fraud_aml.db')
cursor = conn.cursor()

# Create tables
cursor.execute('''
CREATE TABLE IF NOT EXISTS relationship_managers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    department TEXT,
    address TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    address TEXT,
    phone TEXT,
    ssn TEXT,
    rm_id INTEGER,
    FOREIGN KEY (rm_id) REFERENCES relationship_managers (id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    rm_id INTEGER,
    amount REAL,
    transaction_date TEXT,
    transaction_type TEXT,
    description TEXT,
    details TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers (id),
    FOREIGN KEY (rm_id) REFERENCES relationship_managers (id)
)
''')

# Generate Relationship Managers
for i in range(10):
    name = fake.name()
    email = fake.email()
    department = fake.job()
    address = fake.address().replace('\n', ', ')
    cursor.execute('INSERT INTO relationship_managers (name, email, department, address) VALUES (?, ?, ?, ?)', (name, email, department, address))

# Generate Customers
rm_ids = [row[0] for row in cursor.execute('SELECT id FROM relationship_managers').fetchall()]

for i in range(100):
    name = fake.name()
    email = fake.email()
    address = fake.address().replace('\n', ', ')
    phone = fake.phone_number()
    ssn = fake.ssn()
    rm_id = random.choice(rm_ids)
    cursor.execute('INSERT INTO customers (name, email, address, phone, ssn, rm_id) VALUES (?, ?, ?, ?, ?, ?)', (name, email, address, phone, ssn, rm_id))

# Generate Transactions (10 years historical)
customer_ids = [row[0] for row in cursor.execute('SELECT id FROM customers').fetchall()]
start_date = datetime(2015, 1, 1)
end_date = datetime(2025, 12, 31)

for i in range(10000):
    customer_id = random.choice(customer_ids)
    rm_id = random.choice(rm_ids)
    amount = round(random.uniform(10, 10000), 2)
    random_date = start_date + timedelta(days=random.randint(0, (end_date - start_date).days))
    transaction_date = random_date.strftime('%Y-%m-%d')
    transaction_type = random.choice(['deposit', 'withdrawal', 'transfer'])
    description = fake.sentence()
    details = fake.sentence()
    cursor.execute('INSERT INTO transactions (customer_id, rm_id, amount, transaction_date, transaction_type, description, details) VALUES (?, ?, ?, ?, ?, ?, ?)', (customer_id, rm_id, amount, transaction_date, transaction_type, description, details))

# Commit and close
conn.commit()

# Query and display some sample data
print("\nSample Relationship Managers:")
rms = cursor.execute('SELECT name, email, address FROM relationship_managers LIMIT 5').fetchall()
for rm in rms:
    print(f"Name: {rm[0]}, Email: {rm[1]}, Address: {rm[2]}")

print("\nSample Transactions with RM details:")
print("\nSample Transactions with RM details:")
transactions = cursor.execute('SELECT t.amount, t.transaction_date, t.transaction_type, t.description, t.details, rm.name, rm.email FROM transactions t JOIN relationship_managers rm ON t.rm_id = rm.id LIMIT 10').fetchall()
for tx in transactions:
    print(f"Amount: {tx[0]}, Date: {tx[1]}, Type: {tx[2]}, Description: {tx[3]}, Details: {tx[4]}, RM Name: {tx[5]}, RM Email: {tx[6]}")

conn.close()

print("Database created and populated with dummy data.")