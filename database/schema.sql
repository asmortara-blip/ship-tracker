-- ============================================================================
-- Ship Tracker — PostgreSQL Schema
-- Full database for a modern web application replacing Streamlit
-- ============================================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";      -- fuzzy text search
CREATE EXTENSION IF NOT EXISTS "btree_gist";   -- range/exclusion constraints

-- ============================================================================
-- CORE REFERENCE DATA
-- ============================================================================

-- Ports: 25+ global ports with coordinates and metadata
CREATE TABLE ports (
    locode          VARCHAR(5) PRIMARY KEY,          -- UN/LOCODE e.g. "CNSHA"
    name            VARCHAR(100) NOT NULL,
    region          VARCHAR(50) NOT NULL,             -- "Asia East", "Europe", etc.
    country_iso3    CHAR(3) NOT NULL,
    country_numeric CHAR(3),
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    bbox_lat_min    DOUBLE PRECISION,
    bbox_lat_max    DOUBLE PRECISION,
    bbox_lon_min    DOUBLE PRECISION,
    bbox_lon_max    DOUBLE PRECISION,
    tier            SMALLINT DEFAULT 1,               -- 1=top10, 2=major, 3=regional
    timezone        VARCHAR(50),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ports_region ON ports(region);
CREATE INDEX idx_ports_country ON ports(country_iso3);
CREATE INDEX idx_ports_coords ON ports USING gist (
    point(lon, lat)
);

-- Shipping routes: 17+ major trade lanes
CREATE TABLE routes (
    id              VARCHAR(50) PRIMARY KEY,           -- "transpacific_eb"
    name            VARCHAR(100) NOT NULL,
    origin_region   VARCHAR(50) NOT NULL,
    dest_region     VARCHAR(50) NOT NULL,
    origin_locode   VARCHAR(5) REFERENCES ports(locode),
    dest_locode     VARCHAR(5) REFERENCES ports(locode),
    transit_days    SMALLINT,
    fbx_index       VARCHAR(20),                       -- Freightos Baltic Index code
    description     TEXT,
    est_annual_volume_usd_bn NUMERIC(10,1),            -- estimated annual trade $B
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_routes_origin ON routes(origin_region);
CREATE INDEX idx_routes_dest ON routes(dest_region);

-- Commodity / cargo categories
CREATE TABLE commodity_categories (
    id              VARCHAR(30) PRIMARY KEY,            -- "electronics", "machinery"
    label           VARCHAR(50) NOT NULL,               -- "Electronics", "Machinery"
    hs_codes        TEXT[],                             -- HS-4 codes array
    shipping_mode   VARCHAR(50),                        -- "standard container", "bulk"
    seasonal_peak   SMALLINT CHECK (seasonal_peak BETWEEN 1 AND 12),
    yoy_growth_pct  NUMERIC(5,1),
    demand_sensitivity VARCHAR(20),                     -- "high", "moderate", "low"
    color_hex       CHAR(7),                            -- for UI rendering
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Route-to-commodity mapping (what each route carries)
CREATE TABLE route_cargo_mix (
    route_id        VARCHAR(50) REFERENCES routes(id),
    commodity_id    VARCHAR(30) REFERENCES commodity_categories(id),
    weight_pct      NUMERIC(5,2) CHECK (weight_pct BETWEEN 0 AND 100),
    source          VARCHAR(20) DEFAULT 'inferred',     -- "inferred", "comtrade", "manual"
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (route_id, commodity_id)
);

-- ============================================================================
-- VESSEL & FLEET DATA
-- ============================================================================

CREATE TYPE vessel_type AS ENUM (
    'container', 'bulk_carrier', 'tanker', 'lng_carrier',
    'lpg_carrier', 'roro', 'general_cargo', 'chemical_tanker', 'other'
);

CREATE TABLE vessels (
    imo_number      VARCHAR(10) PRIMARY KEY,
    mmsi            VARCHAR(12) UNIQUE,
    name            VARCHAR(100) NOT NULL,
    vessel_type     vessel_type NOT NULL,
    flag_country    CHAR(3),
    built_year      SMALLINT,
    dwt_tonnes      INTEGER,                            -- deadweight tonnage
    teu_capacity    INTEGER,                            -- for container ships
    length_m        NUMERIC(6,1),
    beam_m          NUMERIC(5,1),
    draft_m         NUMERIC(4,1),
    owner           VARCHAR(150),
    operator        VARCHAR(150),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_vessels_type ON vessels(vessel_type);
CREATE INDEX idx_vessels_name ON vessels USING gin(name gin_trgm_ops);

-- AIS position reports (time-series, partitioned by month)
CREATE TABLE vessel_positions (
    id              BIGSERIAL,
    imo_number      VARCHAR(10) REFERENCES vessels(imo_number),
    mmsi            VARCHAR(12),
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    speed_knots     NUMERIC(5,1),
    heading         SMALLINT,
    course          SMALLINT,
    nav_status      VARCHAR(30),
    destination     VARCHAR(50),
    eta             TIMESTAMPTZ,
    nearest_port    VARCHAR(5) REFERENCES ports(locode),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, recorded_at)
) PARTITION BY RANGE (recorded_at);

-- Create monthly partitions (extend as needed)
CREATE TABLE vessel_positions_2026_01 PARTITION OF vessel_positions
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE vessel_positions_2026_02 PARTITION OF vessel_positions
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE vessel_positions_2026_03 PARTITION OF vessel_positions
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE vessel_positions_2026_04 PARTITION OF vessel_positions
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE vessel_positions_2026_05 PARTITION OF vessel_positions
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE vessel_positions_2026_06 PARTITION OF vessel_positions
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX idx_vessel_pos_imo ON vessel_positions(imo_number, recorded_at DESC);
CREATE INDEX idx_vessel_pos_port ON vessel_positions(nearest_port, recorded_at DESC);
CREATE INDEX idx_vessel_pos_coords ON vessel_positions USING gist (
    point(lon, lat)
);

-- ============================================================================
-- MARKET DATA (TIME-SERIES)
-- ============================================================================

-- Freight rate indices (BDI, SCFI, WCI, FBX routes, etc.)
CREATE TABLE freight_rates (
    id              BIGSERIAL PRIMARY KEY,
    index_code      VARCHAR(20) NOT NULL,              -- "BDI", "SCFI", "FBX01", "WCI"
    index_name      VARCHAR(100),
    value           NUMERIC(12,2) NOT NULL,
    unit            VARCHAR(20) DEFAULT 'USD',          -- "USD", "index_points"
    change_1d       NUMERIC(10,2),
    change_1d_pct   NUMERIC(6,2),
    change_1w       NUMERIC(10,2),
    change_1w_pct   NUMERIC(6,2),
    recorded_date   DATE NOT NULL,
    source          VARCHAR(50),                        -- "freightos", "baltic_exchange"
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (index_code, recorded_date)
);

CREATE INDEX idx_freight_code_date ON freight_rates(index_code, recorded_date DESC);

-- Route-level spot rates
CREATE TABLE route_rates (
    id              BIGSERIAL PRIMARY KEY,
    route_id        VARCHAR(50) REFERENCES routes(id),
    rate_usd_feu    NUMERIC(10,2),
    rate_usd_teu    NUMERIC(10,2),
    contract_rate   NUMERIC(10,2),
    spot_contract_spread NUMERIC(8,2),
    recorded_date   DATE NOT NULL,
    source          VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (route_id, recorded_date)
);

CREATE INDEX idx_route_rates_date ON route_rates(route_id, recorded_date DESC);

-- Macro economic indicators (FRED, IMF, OECD data)
CREATE TABLE macro_indicators (
    id              BIGSERIAL PRIMARY KEY,
    series_code     VARCHAR(30) NOT NULL,               -- "CPIAUCSL", "DCOILWTICO"
    series_name     VARCHAR(150),
    category        VARCHAR(50),                        -- "inflation", "energy", "trade", "rates"
    value           NUMERIC(18,4) NOT NULL,
    unit            VARCHAR(30),
    frequency       VARCHAR(10) DEFAULT 'monthly',      -- "daily", "weekly", "monthly", "quarterly"
    recorded_date   DATE NOT NULL,
    source          VARCHAR(30),                        -- "FRED", "IMF", "OECD"
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (series_code, recorded_date)
);

CREATE INDEX idx_macro_code_date ON macro_indicators(series_code, recorded_date DESC);
CREATE INDEX idx_macro_category ON macro_indicators(category, recorded_date DESC);

-- Stock / equity prices (shipping stocks + sector ETFs)
CREATE TABLE stock_prices (
    id              BIGSERIAL PRIMARY KEY,
    ticker          VARCHAR(10) NOT NULL,
    company_name    VARCHAR(100),
    open            NUMERIC(10,2),
    high            NUMERIC(10,2),
    low             NUMERIC(10,2),
    close           NUMERIC(10,2) NOT NULL,
    adj_close       NUMERIC(10,2),
    volume          BIGINT,
    change_pct      NUMERIC(6,2),
    market_cap_m    NUMERIC(12,1),
    pe_ratio        NUMERIC(8,2),
    dividend_yield  NUMERIC(6,3),
    recorded_date   DATE NOT NULL,
    source          VARCHAR(30) DEFAULT 'yfinance',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ticker, recorded_date)
);

CREATE INDEX idx_stock_ticker_date ON stock_prices(ticker, recorded_date DESC);

-- Currency / FX rates
CREATE TABLE fx_rates (
    id              BIGSERIAL PRIMARY KEY,
    pair            VARCHAR(10) NOT NULL,                -- "USD/CNY", "EUR/USD"
    rate            NUMERIC(12,6) NOT NULL,
    change_1d_pct   NUMERIC(6,3),
    recorded_date   DATE NOT NULL,
    source          VARCHAR(30) DEFAULT 'FRED',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (pair, recorded_date)
);

CREATE INDEX idx_fx_pair_date ON fx_rates(pair, recorded_date DESC);

-- ============================================================================
-- PORT ANALYTICS
-- ============================================================================

-- Port demand scores (computed by demand analyzer)
CREATE TABLE port_demand (
    id              BIGSERIAL PRIMARY KEY,
    port_locode     VARCHAR(5) REFERENCES ports(locode),
    demand_score    NUMERIC(4,3) CHECK (demand_score BETWEEN 0 AND 1),
    congestion_index NUMERIC(4,3) CHECK (congestion_index BETWEEN 0 AND 1),
    vessel_count    INTEGER,
    vessel_type_breakdown JSONB,                        -- {"container": 45, "bulk": 12, ...}
    avg_wait_days   NUMERIC(5,1),
    throughput_teu  INTEGER,
    has_real_data   BOOLEAN DEFAULT FALSE,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (port_locode, computed_at)
);

CREATE INDEX idx_port_demand_port ON port_demand(port_locode, computed_at DESC);

-- Port events (strikes, weather, congestion spikes)
CREATE TABLE port_events (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    port_locode     VARCHAR(5) REFERENCES ports(locode),
    event_type      VARCHAR(30) NOT NULL,               -- "weather", "strike", "congestion", "policy"
    severity        VARCHAR(10) CHECK (severity IN ('LOW', 'MODERATE', 'HIGH', 'CRITICAL')),
    title           VARCHAR(200) NOT NULL,
    description     TEXT,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE,
    source          VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_port_events_port ON port_events(port_locode, is_active, created_at DESC);

-- ============================================================================
-- TRADE FLOW DATA
-- ============================================================================

-- Country-level trade flows (from UN Comtrade / WITS)
CREATE TABLE trade_flows (
    id              BIGSERIAL PRIMARY KEY,
    reporter_iso3   CHAR(3) NOT NULL,
    partner_iso3    CHAR(3) NOT NULL,
    flow_direction  VARCHAR(6) CHECK (flow_direction IN ('export', 'import')),
    commodity_id    VARCHAR(30) REFERENCES commodity_categories(id),
    hs_code         VARCHAR(6),                         -- HS-4 or HS-6
    value_usd       NUMERIC(16,2),
    weight_kg       NUMERIC(16,2),
    year            SMALLINT NOT NULL,
    month           SMALLINT,                           -- NULL for annual data
    source          VARCHAR(30) DEFAULT 'comtrade',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_trade_reporter ON trade_flows(reporter_iso3, year DESC);
CREATE INDEX idx_trade_commodity ON trade_flows(commodity_id, year DESC);
CREATE INDEX idx_trade_pair ON trade_flows(reporter_iso3, partner_iso3, year DESC);

-- Route-level trade volume estimates (aggregated from trade_flows + port weights)
CREATE TABLE route_trade_volumes (
    id              BIGSERIAL PRIMARY KEY,
    route_id        VARCHAR(50) REFERENCES routes(id),
    commodity_id    VARCHAR(30) REFERENCES commodity_categories(id),
    est_value_usd   NUMERIC(16,2),
    est_teu         INTEGER,
    year            SMALLINT NOT NULL,
    quarter         SMALLINT CHECK (quarter BETWEEN 1 AND 4),
    source          VARCHAR(30) DEFAULT 'inferred',
    computed_at     TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (route_id, commodity_id, year, quarter)
);

-- ============================================================================
-- INTELLIGENCE ENGINE
-- ============================================================================

-- Insights (generated by scoring engine)
CREATE TABLE insights (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    category        VARCHAR(30) NOT NULL,               -- "CONVERGENCE", "ROUTE", "PORT_DEMAND", "MACRO"
    score           NUMERIC(4,3) CHECK (score BETWEEN 0 AND 1),
    action          VARCHAR(20),                        -- "Prioritize", "Monitor", "Watch", "Caution", "Avoid"
    detail          TEXT,
    supporting_signals JSONB,                           -- [{signal, weight, value}, ...]
    ports_involved  TEXT[],
    routes_involved TEXT[],
    stocks_affected TEXT[],
    data_freshness_warning BOOLEAN DEFAULT FALSE,
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_insights_active ON insights(is_active, score DESC, generated_at DESC);
CREATE INDEX idx_insights_category ON insights(category, is_active);

-- Alerts (triggered by alert engine)
CREATE TABLE alerts (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    message         TEXT,
    severity        VARCHAR(10) CHECK (severity IN ('LOW', 'MODERATE', 'HIGH', 'CRITICAL')),
    category        VARCHAR(30),                        -- "rate_spike", "congestion", "geopolitical", "weather"
    source_insight  UUID REFERENCES insights(id),
    port_locode     VARCHAR(5) REFERENCES ports(locode),
    route_id        VARCHAR(50) REFERENCES routes(id),
    is_read         BOOLEAN DEFAULT FALSE,
    is_dismissed    BOOLEAN DEFAULT FALSE,
    triggered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at         TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ
);

CREATE INDEX idx_alerts_unread ON alerts(is_read, severity, triggered_at DESC)
    WHERE NOT is_dismissed;

-- Route opportunities (from route optimizer)
CREATE TABLE route_opportunities (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    route_id        VARCHAR(50) REFERENCES routes(id),
    opportunity_score NUMERIC(4,3) CHECK (opportunity_score BETWEEN 0 AND 1),
    opportunity_label VARCHAR(20),                      -- "Strong", "Moderate", "Weak"
    current_rate_usd_feu NUMERIC(10,2),
    rate_trend      VARCHAR(10),                        -- "Rising", "Stable", "Falling"
    demand_imbalance NUMERIC(6,3),
    rate_momentum   NUMERIC(6,3),
    macro_tailwind  NUMERIC(6,3),
    rationale       TEXT,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_route_opps_score ON route_opportunities(opportunity_score DESC, computed_at DESC);

-- Market regime (bull/bear/transition)
CREATE TABLE market_regimes (
    id              BIGSERIAL PRIMARY KEY,
    regime          VARCHAR(20) NOT NULL,               -- "bull", "bear", "transition"
    confidence      NUMERIC(4,3),
    sub_regime      VARCHAR(30),
    cycle_phase     VARCHAR(30),                        -- "expansion", "peak", "contraction", "trough"
    cycle_duration_days INTEGER,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Momentum rankings
CREATE TABLE momentum_rankings (
    id              BIGSERIAL PRIMARY KEY,
    route_id        VARCHAR(50) REFERENCES routes(id),
    ticker          VARCHAR(10),
    entity_type     VARCHAR(10) CHECK (entity_type IN ('route', 'stock')),
    momentum_score  NUMERIC(6,3),
    rank            SMALLINT,
    signal          VARCHAR(20),                        -- "STRONG_BUY", "BUY", "HOLD", "SELL"
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_momentum_date ON momentum_rankings(computed_at DESC);

-- ============================================================================
-- GEOPOLITICAL & RISK
-- ============================================================================

CREATE TABLE geopolitical_events (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    title           VARCHAR(200) NOT NULL,
    event_type      VARCHAR(30),                        -- "conflict", "sanctions", "policy", "trade_dispute"
    severity        VARCHAR(10) CHECK (severity IN ('LOW', 'MODERATE', 'HIGH', 'CRITICAL')),
    description     TEXT,
    affected_routes TEXT[],
    affected_ports  TEXT[],
    impact_score    NUMERIC(4,3),
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    is_active       BOOLEAN DEFAULT TRUE,
    source          VARCHAR(50),
    source_url      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_geo_events_active ON geopolitical_events(is_active, severity, created_at DESC);

-- Chokepoint status (Suez, Panama, Malacca, etc.)
CREATE TABLE chokepoint_status (
    id              BIGSERIAL PRIMARY KEY,
    chokepoint      VARCHAR(50) NOT NULL,               -- "suez_canal", "panama_canal", "malacca_strait"
    transit_count   INTEGER,
    avg_wait_hours  NUMERIC(6,1),
    capacity_pct    NUMERIC(5,1),
    is_disrupted    BOOLEAN DEFAULT FALSE,
    disruption_note TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chokepoint, recorded_at)
);

-- ============================================================================
-- NEWS & SENTIMENT
-- ============================================================================

CREATE TABLE news_articles (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    title           VARCHAR(300) NOT NULL,
    source_name     VARCHAR(100),
    source_url      TEXT,
    published_at    TIMESTAMPTZ,
    summary         TEXT,
    sentiment_score NUMERIC(4,3) CHECK (sentiment_score BETWEEN -1 AND 1),
    sentiment_label VARCHAR(10),                        -- "positive", "negative", "neutral"
    relevance_score NUMERIC(4,3),
    tags            TEXT[],                             -- ["rates", "congestion", "suez", ...]
    ports_mentioned TEXT[],
    routes_mentioned TEXT[],
    tickers_mentioned TEXT[],
    is_processed    BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_news_published ON news_articles(published_at DESC);
CREATE INDEX idx_news_sentiment ON news_articles(sentiment_label, published_at DESC);
CREATE INDEX idx_news_tags ON news_articles USING gin(tags);

-- ============================================================================
-- SUPPLY CHAIN HEALTH
-- ============================================================================

CREATE TABLE supply_chain_scores (
    id              BIGSERIAL PRIMARY KEY,
    overall_score   NUMERIC(4,3) CHECK (overall_score BETWEEN 0 AND 1),
    port_health     NUMERIC(4,3),
    route_health    NUMERIC(4,3),
    rate_stability  NUMERIC(4,3),
    geopolitical_risk NUMERIC(4,3),
    weather_risk    NUMERIC(4,3),
    schedule_reliability NUMERIC(4,3),
    detail          JSONB,                              -- granular breakdown
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- REPORTS & SNAPSHOTS
-- ============================================================================

-- Daily market snapshots for historical dashboards
CREATE TABLE daily_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL UNIQUE,
    market_tone     VARCHAR(10),                        -- "bullish", "neutral", "bearish"
    bdi_value       NUMERIC(10,2),
    scfi_value      NUMERIC(10,2),
    wci_value       NUMERIC(10,2),
    avg_port_demand NUMERIC(4,3),
    active_alerts   SMALLINT,
    high_conviction_signals SMALLINT,
    total_signals   SMALLINT,
    strong_routes   SMALLINT,
    regime          VARCHAR(20),
    top_insight     TEXT,
    snapshot_data   JSONB,                              -- full JSON dump for archival
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_snapshots_date ON daily_snapshots(snapshot_date DESC);

-- Generated reports
CREATE TABLE reports (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    report_type     VARCHAR(30) NOT NULL,               -- "daily_digest", "investor", "weekly_summary"
    title           VARCHAR(200),
    format          VARCHAR(10) DEFAULT 'html',         -- "html", "pdf"
    content_html    TEXT,
    content_url     TEXT,                               -- S3/storage link for PDFs
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    period_start    DATE,
    period_end      DATE
);

-- ============================================================================
-- USER & SESSION (for the web app)
-- ============================================================================

CREATE TABLE users (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    display_name    VARCHAR(100),
    role            VARCHAR(20) DEFAULT 'viewer',       -- "viewer", "analyst", "admin"
    avatar_url      TEXT,
    preferences     JSONB DEFAULT '{}',                 -- theme, default lookback, watchlist, etc.
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- User watchlists
CREATE TABLE watchlist_items (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
    item_type       VARCHAR(10) CHECK (item_type IN ('ticker', 'port', 'route')),
    item_id         VARCHAR(50) NOT NULL,               -- ticker symbol, locode, or route id
    display_name    VARCHAR(100),
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, item_type, item_id)
);

-- User alert preferences
CREATE TABLE alert_preferences (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
    alert_category  VARCHAR(30),
    min_severity    VARCHAR(10) DEFAULT 'MODERATE',
    email_notify    BOOLEAN DEFAULT FALSE,
    push_notify     BOOLEAN DEFAULT TRUE,
    is_enabled      BOOLEAN DEFAULT TRUE,
    UNIQUE (user_id, alert_category)
);

-- Saved views / dashboard configurations
CREATE TABLE saved_views (
    id              UUID DEFAULT uuid_generate_v4() PRIMARY KEY,
    user_id         UUID REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    view_type       VARCHAR(30),                        -- "dashboard", "flow_map", "custom"
    config          JSONB NOT NULL,                     -- filters, layout, selected metrics
    is_default      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- DATA PIPELINE TRACKING
-- ============================================================================

CREATE TABLE data_source_status (
    id              BIGSERIAL PRIMARY KEY,
    source_name     VARCHAR(50) NOT NULL,               -- "yfinance", "FRED", "comtrade", "ais"
    source_type     VARCHAR(20),                        -- "API", "scrape", "manual"
    endpoint        VARCHAR(200),
    last_fetch_at   TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    records_fetched INTEGER,
    fetch_duration_ms INTEGER,
    status          VARCHAR(10) DEFAULT 'unknown',      -- "healthy", "stale", "error", "unknown"
    error_message   TEXT,
    ttl_hours       NUMERIC(6,1),
    api_key_set     BOOLEAN DEFAULT FALSE,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_name)
);

CREATE TABLE fetch_log (
    id              BIGSERIAL PRIMARY KEY,
    source_name     VARCHAR(50) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(10),                        -- "success", "error", "timeout"
    records_fetched INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    error_message   TEXT
);

CREATE INDEX idx_fetch_log_source ON fetch_log(source_name, started_at DESC);

-- ============================================================================
-- MATERIALIZED VIEWS (for dashboard performance)
-- ============================================================================

-- Latest freight rates (one row per index, most recent value)
CREATE MATERIALIZED VIEW mv_latest_freight AS
SELECT DISTINCT ON (index_code)
    index_code, index_name, value, unit,
    change_1d, change_1d_pct, change_1w, change_1w_pct,
    recorded_date, source
FROM freight_rates
ORDER BY index_code, recorded_date DESC;

CREATE UNIQUE INDEX idx_mv_freight ON mv_latest_freight(index_code);

-- Latest port demand (one row per port)
CREATE MATERIALIZED VIEW mv_latest_port_demand AS
SELECT DISTINCT ON (port_locode)
    port_locode, demand_score, congestion_index,
    vessel_count, vessel_type_breakdown, avg_wait_days,
    throughput_teu, has_real_data, computed_at
FROM port_demand
ORDER BY port_locode, computed_at DESC;

CREATE UNIQUE INDEX idx_mv_port_demand ON mv_latest_port_demand(port_locode);

-- Latest stock prices (one row per ticker)
CREATE MATERIALIZED VIEW mv_latest_stocks AS
SELECT DISTINCT ON (ticker)
    ticker, company_name, close, change_pct,
    volume, market_cap_m, pe_ratio, dividend_yield,
    recorded_date
FROM stock_prices
ORDER BY ticker, recorded_date DESC;

CREATE UNIQUE INDEX idx_mv_stocks ON mv_latest_stocks(ticker);

-- Active insights summary
CREATE MATERIALIZED VIEW mv_active_insights AS
SELECT id, title, category, score, action, detail,
       ports_involved, routes_involved, stocks_affected,
       generated_at
FROM insights
WHERE is_active = TRUE
  AND (expires_at IS NULL OR expires_at > NOW())
ORDER BY score DESC;

-- Dashboard KPI summary (single row, refreshed periodically)
CREATE MATERIALIZED VIEW mv_dashboard_kpis AS
SELECT
    (SELECT value FROM mv_latest_freight WHERE index_code = 'BDI') AS bdi,
    (SELECT value FROM mv_latest_freight WHERE index_code = 'SCFI') AS scfi,
    (SELECT value FROM mv_latest_freight WHERE index_code = 'WCI') AS wci,
    (SELECT AVG(demand_score) FROM mv_latest_port_demand WHERE has_real_data) AS avg_port_demand,
    (SELECT COUNT(*) FROM alerts WHERE NOT is_read AND NOT is_dismissed) AS active_alerts,
    (SELECT COUNT(*) FROM mv_active_insights WHERE score >= 0.70) AS high_conviction,
    (SELECT COUNT(*) FROM mv_active_insights) AS total_signals,
    (SELECT COUNT(*) FROM route_opportunities WHERE opportunity_label = 'Strong'
        AND computed_at > NOW() - INTERVAL '24 hours') AS strong_routes,
    NOW() AS refreshed_at;

-- ============================================================================
-- REFRESH FUNCTION (call from cron / pg_cron)
-- ============================================================================

CREATE OR REPLACE FUNCTION refresh_dashboard_views()
RETURNS VOID AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_latest_freight;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_latest_port_demand;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_latest_stocks;
    REFRESH MATERIALIZED VIEW mv_active_insights;
    REFRESH MATERIALIZED VIEW mv_dashboard_kpis;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Score to color mapping (matches UI palette)
CREATE OR REPLACE FUNCTION score_color(score NUMERIC)
RETURNS VARCHAR(7) AS $$
BEGIN
    IF score >= 0.70 THEN RETURN '#2e9e6e'; END IF;
    IF score >= 0.45 THEN RETURN '#c9962b'; END IF;
    RETURN '#c0392b';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Market tone from average demand
CREATE OR REPLACE FUNCTION market_tone(avg_demand NUMERIC)
RETURNS VARCHAR(10) AS $$
BEGIN
    IF avg_demand >= 0.65 THEN RETURN 'bullish'; END IF;
    IF avg_demand >= 0.45 THEN RETURN 'neutral'; END IF;
    IF avg_demand > 0 THEN RETURN 'bearish'; END IF;
    RETURN 'loading';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ports_updated BEFORE UPDATE ON ports
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
CREATE TRIGGER trg_vessels_updated BEFORE UPDATE ON vessels
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- ============================================================================
-- SEED: Commodity categories
-- ============================================================================

INSERT INTO commodity_categories (id, label, hs_codes, shipping_mode, seasonal_peak, yoy_growth_pct, demand_sensitivity, color_hex)
VALUES
    ('electronics', 'Electronics',    ARRAY['8471','8517','8542','8541','8536','8544'], 'standard container',    9,  6.2, 'high',     '#3b82f6'),
    ('machinery',   'Machinery',      ARRAY['8413','8479','8431','8483','8501','8503'], 'heavy-lift/OOG',        3,  3.8, 'moderate', '#8b5cf6'),
    ('automotive',  'Automotive',     ARRAY['8703','8708','8716','8701','8702'],        'RoRo or container',     4,  2.1, 'high',     '#06b6d4'),
    ('apparel',     'Apparel',        ARRAY['6109','6110','6204','6203','6101','6102'], 'standard container',    7,  1.5, 'moderate', '#ec4899'),
    ('chemicals',   'Chemicals',      ARRAY['2902','2903','2905','3901','3902','2907'], 'ISO tank/specialized',  2,  4.3, 'low',      '#f59e0b'),
    ('agriculture', 'Agriculture',    ARRAY['1001','1201','0901','0902','1005','1507'], 'refrigerated/bulk',    10,  5.7, 'moderate', '#22c55e'),
    ('metals',      'Metals & Steel', ARRAY['7208','7209','7210','7213','7214','7225'], 'bulk/break-bulk',       5, -1.2, 'high',     '#f97316')
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- SEED: Data source registry
-- ============================================================================

INSERT INTO data_source_status (source_name, source_type, endpoint, ttl_hours, status)
VALUES
    ('yfinance',     'API',         'yahoo finance',                   1,    'unknown'),
    ('FRED',         'API',         'api.stlouisfed.org',             24,    'unknown'),
    ('WorldBank',    'API',         'api.worldbank.org',             168,    'unknown'),
    ('Comtrade',     'API',         'wits.worldbank.org',            168,    'unknown'),
    ('Freightos',    'API',         'fbx.freightos.com/api',          24,    'unknown'),
    ('AIS',          'API',         'aisstream.io / IMF PortWatch',    0.5,  'unknown'),
    ('NewsAPI',      'API',         'newsapi.org',                    12,    'unknown'),
    ('AlphaVantage', 'API',         'alphavantage.co',                24,    'unknown')
ON CONFLICT (source_name) DO NOTHING;
