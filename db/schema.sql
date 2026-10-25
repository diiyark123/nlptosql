-- Aircraft maintenance schema. Column COMMENTs are read by the app and passed
-- to the LLM, so keep them descriptive - they directly improve query accuracy.

CREATE TABLE aircraft (
    aircraft_id          INT AUTO_INCREMENT PRIMARY KEY,
    tail_number          VARCHAR(10)  NOT NULL UNIQUE COMMENT 'Aircraft registration, e.g. VT-RMA',
    model                VARCHAR(40)  NOT NULL COMMENT 'Aircraft type, e.g. A320neo, B737-800, ATR 72-600',
    manufacturer         VARCHAR(40)  NOT NULL COMMENT 'Airbus, Boeing, ATR, Embraer',
    year_of_manufacture  SMALLINT     NOT NULL,
    operator             VARCHAR(60)  NOT NULL COMMENT 'Airline operating the aircraft',
    base_airport         CHAR(3)      NOT NULL COMMENT 'IATA code of home maintenance base',
    total_flight_hours   INT          NOT NULL COMMENT 'Cumulative airframe flight hours',
    status               ENUM('Active','In Maintenance','Grounded','Retired') NOT NULL
) COMMENT 'Fleet register: one row per aircraft';

CREATE TABLE technicians (
    technician_id   INT AUTO_INCREMENT PRIMARY KEY,
    full_name       VARCHAR(80)  NOT NULL,
    license_number  VARCHAR(20)  NOT NULL UNIQUE COMMENT 'Aircraft Maintenance Engineer licence number',
    specialization  VARCHAR(40)  NOT NULL COMMENT 'Engines, Avionics, Airframe, Hydraulics, Electrical, Landing Gear',
    base_airport    CHAR(3)      NOT NULL COMMENT 'IATA code where the technician is based',
    hire_date       DATE         NOT NULL
) COMMENT 'Licensed maintenance engineers';

CREATE TABLE components (
    component_id         INT AUTO_INCREMENT PRIMARY KEY,
    aircraft_id          INT          NULL COMMENT 'Aircraft the part is installed on; NULL if removed / in stores',
    part_number          VARCHAR(30)  NOT NULL COMMENT 'Manufacturer part number (P/N)',
    serial_number        VARCHAR(30)  NOT NULL UNIQUE COMMENT 'Unique serial number (S/N)',
    name                 VARCHAR(80)  NOT NULL COMMENT 'Component description, e.g. CFM LEAP-1A Engine, Main Landing Gear',
    category             ENUM('Engine','Landing Gear','Avionics','Hydraulics','APU','Airframe','Electrical') NOT NULL,
    manufacturer         VARCHAR(40)  NOT NULL,
    install_date         DATE         NULL,
    hours_since_install  INT          NOT NULL DEFAULT 0 COMMENT 'Flight hours accumulated since installation',
    life_limit_hours     INT          NULL COMMENT 'Hard life limit in flight hours; NULL = on-condition part',
    status               ENUM('Serviceable','Due for Inspection','Unserviceable','Removed') NOT NULL,
    CONSTRAINT fk_component_aircraft FOREIGN KEY (aircraft_id) REFERENCES aircraft(aircraft_id)
) COMMENT 'Tracked rotable / life-limited parts. Remaining life = life_limit_hours - hours_since_install';

CREATE TABLE inspections (
    inspection_id    INT AUTO_INCREMENT PRIMARY KEY,
    aircraft_id      INT  NOT NULL,
    component_id     INT  NULL COMMENT 'Set when the inspection targets a specific component',
    technician_id    INT  NULL COMMENT 'Inspector who signed off; NULL while still pending',
    inspection_type  ENUM('A-Check','B-Check','C-Check','D-Check','Visual','NDT','Borescope') NOT NULL,
    scheduled_date   DATE NOT NULL,
    completed_date   DATE NULL COMMENT 'NULL = not yet completed. Overdue = completed_date IS NULL AND scheduled_date < CURDATE()',
    result           ENUM('Pass','Pass with Findings','Fail','Pending') NOT NULL,
    findings         TEXT NULL COMMENT 'Free-text inspector findings',
    CONSTRAINT fk_insp_aircraft   FOREIGN KEY (aircraft_id)   REFERENCES aircraft(aircraft_id),
    CONSTRAINT fk_insp_component  FOREIGN KEY (component_id)  REFERENCES components(component_id),
    CONSTRAINT fk_insp_technician FOREIGN KEY (technician_id) REFERENCES technicians(technician_id)
) COMMENT 'Scheduled and completed inspections / checks';

CREATE TABLE maintenance_logs (
    log_id         INT AUTO_INCREMENT PRIMARY KEY,
    aircraft_id    INT            NOT NULL,
    component_id   INT            NULL,
    technician_id  INT            NOT NULL,
    log_date       DATE           NOT NULL,
    action_type    ENUM('Repair','Replacement','Lubrication','Adjustment','Overhaul','Software Update','Troubleshooting') NOT NULL,
    defect_code    VARCHAR(10)    NULL COMMENT 'ATA chapter-based defect code, e.g. ATA-32 (landing gear), ATA-72 (engine)',
    description    VARCHAR(255)   NOT NULL,
    labor_hours    DECIMAL(5,1)   NOT NULL,
    parts_cost     DECIMAL(10,2)  NOT NULL DEFAULT 0 COMMENT 'Cost of parts used, in USD',
    is_aog         BOOLEAN        NOT NULL DEFAULT FALSE COMMENT 'TRUE if the aircraft was grounded (Aircraft On Ground) for this work',
    CONSTRAINT fk_log_aircraft   FOREIGN KEY (aircraft_id)   REFERENCES aircraft(aircraft_id),
    CONSTRAINT fk_log_component  FOREIGN KEY (component_id)  REFERENCES components(component_id),
    CONSTRAINT fk_log_technician FOREIGN KEY (technician_id) REFERENCES technicians(technician_id),
    INDEX idx_log_date (log_date)
) COMMENT 'Work performed on aircraft: one row per maintenance task';
