# Agent 1: Data Architect

This agent is responsible for scraping real-time names and emails from the web to generate a relational database. The database includes high-sensitivity customer data, 10 years of historical transaction records, and Relationship Manager details, all linked via Primary and Foreign keys.

## Implementation

- Uses Faker library to generate realistic dummy data for names and emails.
- Creates a SQLite database with the following schema:
  - Customers table
  - Transactions table
  - Relationship Managers table

## Files

- `agent1.py`: Main script for data generation and database population.