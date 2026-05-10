-- Default values
INSERT INTO config.parameters (key, value, description) VALUES
    ('bonus_rate',          '0.05', 'Sports bonus rate (5% of annual gross salary)'),
    ('activity_threshold',  '15',   'Minimum number of activities for wellness days eligibility')
ON CONFLICT (key) DO NOTHING;

-- Authorized commute modes
INSERT INTO config.commute_modes (mode) VALUES
    ('marche/running'),
    ('vélo/trottinette/autres'),
    ('véhicule thermique/électrique'),
    ('transports en commun')
ON CONFLICT (mode) DO NOTHING;


-- Sports reference data with physical constraints
INSERT INTO config.sports (sport, max_speed_kmh, min_duration_min, has_distance) VALUES
    ('Running',         25,   15, true),
    ('Randonnée',       6,    60, true),
    ('Natation',        7,    20, true),
    ('Triathlon',       40,   60, true),
    ('Tennis',          NULL, 30, false),
    ('Badminton',       NULL, 30, false),
    ('Tennis de table', NULL, 20, false),
    ('Escalade',        NULL, 60, false),
    ('Football',        NULL, 30, false),
    ('Basketball',      NULL, 30, false),
    ('Rugby',           NULL, 30, false),
    ('Judo',            NULL, 30, false),
    ('Boxe',            NULL, 20, false),
    ('Équitation',      NULL, 30, false),
    ('Voile',           NULL, 60, false)
ON CONFLICT (sport) DO NOTHING;