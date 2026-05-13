-- Authorized commute modes
INSERT INTO config.commute_modes (mode) VALUES
    ('marche/running'),
    ('vélo/trottinette/autres'),
    ('véhicule thermique/électrique'),
    ('transports en commun')
ON CONFLICT (mode) DO NOTHING;