-- Business parameters table
CREATE TABLE IF NOT EXISTS config.parameters (
    key VARCHAR(100) PRIMARY KEY,
    value VARCHAR(255) NOT NULL,
    description TEXT
);

-- Default values
INSERT INTO config.parameters (key, value, description) VALUES
    ('bonus_rate', '0.05', 'Sports bonus rate (5% of annual gross salary)'),
    ('activity_threshold', '15', 'Minimum number of activities for wellness days eligibility')
ON CONFLICT (key) DO NOTHING;