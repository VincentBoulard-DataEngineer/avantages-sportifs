-- Default values
INSERT INTO config.parameters (key, value, description) VALUES
    ('bonus_rate',          '0.05', 'Sports bonus rate (5% of annual gross salary)'),
    ('activity_threshold',  '15',   'Minimum number of activities for wellness days eligibility')
ON CONFLICT (key) DO NOTHING;