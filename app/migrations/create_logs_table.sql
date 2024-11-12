-- Migration: Create Application Logs Table

-- Create enum for log levels
CREATE TYPE log_level AS ENUM ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL');

-- Create enum for request types
CREATE TYPE request_type AS ENUM ('ITI-47', 'ITI-38', 'ITI-39', 'SECURITY', 'CCDA');

-- Create application logs table
CREATE TABLE IF NOT EXISTS application_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    correlation_id UUID NOT NULL,
    nhs_number VARCHAR(10),
    request_type request_type,
    level log_level NOT NULL,
    module VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    request_details JSONB,
    response_details JSONB,
    extra_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_logs_correlation_id ON application_logs(correlation_id);
CREATE INDEX IF NOT EXISTS idx_logs_nhs_number ON application_logs(nhs_number);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON application_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_logs_request_type ON application_logs(request_type);
CREATE INDEX IF NOT EXISTS idx_logs_level ON application_logs(level);

-- Create table for correlation ID reuse
CREATE TABLE IF NOT EXISTS correlation_mappings (
    id SERIAL PRIMARY KEY,
    nhs_number VARCHAR(10) NOT NULL,
    correlation_id UUID NOT NULL,
    request_type request_type NOT NULL,
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    use_count INTEGER DEFAULT 1,
    UNIQUE(nhs_number, request_type)
);

-- Create indexes for correlation mappings
CREATE INDEX IF NOT EXISTS idx_correlation_nhs_number ON correlation_mappings(nhs_number);
CREATE INDEX IF NOT EXISTS idx_correlation_id ON correlation_mappings(correlation_id);

-- Create function to update last_used timestamp and use_count
CREATE OR REPLACE FUNCTION update_correlation_mapping()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE correlation_mappings
    SET last_used = CURRENT_TIMESTAMP,
        use_count = use_count + 1
    WHERE correlation_id = NEW.correlation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to update correlation mapping on log insert
CREATE TRIGGER tr_update_correlation_mapping
AFTER INSERT ON application_logs
FOR EACH ROW
EXECUTE FUNCTION update_correlation_mapping();

-- Create view for security events
CREATE OR REPLACE VIEW security_events AS
SELECT *
FROM application_logs
WHERE request_type = 'SECURITY'
  AND level IN ('WARNING', 'ERROR')
ORDER BY timestamp DESC;

-- Create view for request tracing
CREATE OR REPLACE VIEW request_traces AS
SELECT 
    correlation_id,
    nhs_number,
    request_type,
    array_agg(message ORDER BY timestamp) as trace_messages,
    min(timestamp) as start_time,
    max(timestamp) as end_time,
    count(*) as event_count
FROM application_logs
GROUP BY correlation_id, nhs_number, request_type;

-- Comments
COMMENT ON TABLE application_logs IS 'Stores all application logs with correlation IDs and request details';
COMMENT ON TABLE correlation_mappings IS 'Maps NHS numbers to correlation IDs for request tracing';
COMMENT ON VIEW security_events IS 'Shows security-related warning and error events';
COMMENT ON VIEW request_traces IS 'Provides request tracing information grouped by correlation ID';
