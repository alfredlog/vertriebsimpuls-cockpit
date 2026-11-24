CREATE SCHEMA IF NOT EXISTS vic;


CREATE TYPE IF NOT EXISTS vic.segment_type AS ENUM (
    'cooperative_bank',   -- Volksbanken / Raiffeisenbanken
    'third_party_bank',   -- Drittbanken
    'corporate',          -- Firmenkunden
    'institutional'       -- assurances, caisses de retraite, Versorgungswerke
);

CREATE TYPE IF NOT EXISTS vic.asset_class_type AS ENUM (
    'government_bond',
    'covered_bond',
    'corporate_bond',
    'rates_derivative',
    'fx_derivative',
    'money_market',
    'structured_product',
    'fund'
);

CREATE TYPE IF NOT EXISTS vic.risk_profile_type AS ENUM (
    'conservative', 
    'balanced', 
    'dynamic'
);

CREATE TABLE IF NOT EXISTS vic.dim_sales_rep ( 
    sales_rep_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    region VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS vic.dim_product (
    product_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    asset_class vic.asset_class_type NOT NULL,
    tenor_years DECIMAL(5,2),
    risk_level INTEGER NOT NULL CHECK(risk_level BETWEEN 1 AND 5),
    is_hedging_product BOOLEAN NOT NULL
);



CREATE TABLE IF NOT EXISTS vic.dim_client (
    client_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    segment vic.segment_type NOT NULL,
    region VARCHAR NOT NULL,
    aum_eur DECIMAL(18,2) NOT NULL,
    risk_profile  vic.risk_profile_type NOT NULL,
    has_floating_rate_debt BOOLEAN NOT NULL,
    sales_rep_id VARCHAR NOT NULL,

    FOREIGN KEY (sales_rep_id)
        REFERENCES vic.dim_sales_rep(sales_rep_id)
);

CREATE TABLE IF NOT EXISTS vic.dim_date (
    date DATE PRIMARY KEY,
    quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    year INTEGER NOT NULL,
    month_name VARCHAR NOT NULL ,
    iso_week INTEGER NOT NULL CHECK (iso_week BETWEEN 1 AND 53),
    weekday VARCHAR NOT NULL,
    is_weekend BOOLEAN NOT NULL,
    is_month_end BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS vic.fact_trades(
    trade_id VARCHAR PRIMARY KEY,
    trade_date DATE NOT NULL,
    notional_eur DECIMAL(18,2) NOT NULL,
    client_id VARCHAR NOT NULL,
    product_id VARCHAR NOT NULL,
        
    FOREIGN KEY (client_id)
        REFERENCES vic.dim_client(client_id),

    FOREIGN KEY (product_id)
        REFERENCES vic.dim_product(product_id),

    FOREIGN KEY (trade_date)
        REFERENCES vic.dim_date(date) 
);

CREATE TABLE IF NOT EXISTS vic.fact_positions(
    position_id VARCHAR PRIMARY KEY,
    start_date DATE NOT NULL,
    maturity_date DATE NOT NULL,
    notional_eur DECIMAL(18,2) NOT NULL,
    client_id VARCHAR NOT NULL,
    product_id VARCHAR NOT NULL,

    FOREIGN KEY (client_id)
        REFERENCES vic.dim_client(client_id),

    FOREIGN KEY (product_id)
        REFERENCES vic.dim_product(product_id)
);