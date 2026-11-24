"""Default reference data. Edit these in the Admin screen after install."""

CALL_TYPES = [
    # code, description, default priority (1 = highest)
    ("ALARM", "Alarm - Burglary/Panic", 2),
    ("ANIMAL", "Animal Complaint", 4),
    ("ASSAULT", "Assault", 1),
    ("ASSIST", "Assist Other Agency", 3),
    ("ASSISTC", "Citizen Assist", 4),
    ("BURG", "Burglary", 2),
    ("CHECK", "Building/Area Check", 5),
    ("CIVIL", "Civil Matter", 4),
    ("DISORD", "Disorderly Conduct", 2),
    ("DISTURB", "Disturbance / Noise", 3),
    ("DOMESTIC", "Domestic Disturbance", 1),
    ("DUI", "DUI / Impaired Driver", 2),
    ("FRAUD", "Fraud / Scam", 4),
    ("HARASS", "Harassment", 3),
    ("LARCENY", "Larceny / Theft", 3),
    ("LOST", "Lost / Found Property", 5),
    ("MENTAL", "Mental Health Crisis", 1),
    ("MISSING", "Missing Person", 2),
    ("MVA", "Motor Vehicle Crash", 2),
    ("MVAINJ", "Motor Vehicle Crash - Injury", 1),
    ("MEDICAL", "Medical Assist", 1),
    ("OVERDOSE", "Overdose", 1),
    ("PARKING", "Parking Complaint", 5),
    ("PROPDMG", "Property Damage / Vandalism", 4),
    ("SERVICE", "Paper Service", 5),
    ("SUSP", "Suspicious Person/Vehicle", 3),
    ("THREAT", "Threats", 2),
    ("TRAFFIC", "Traffic Complaint / Hazard", 3),
    ("TS", "Traffic Stop", 3),
    ("TRESPASS", "Trespass", 3),
    ("WARRANT", "Warrant Service", 3),
    ("WELFARE", "Welfare Check", 2),
    ("WEAPON", "Weapons Complaint", 1),
    ("911HANG", "911 Hang-up / Open Line", 2),
    ("FIRE", "Fire / Smoke Report", 1),
    ("OTHER", "Other", 4),
]

DISPOSITIONS = [
    ("REPORT", "Report Taken"),
    ("ARREST", "Arrest"),
    ("CITATION", "Citation Issued"),
    ("WARNING", "Warning Issued"),
    ("ADVISED", "Advised / Mediated"),
    ("ASSISTED", "Assisted"),
    ("GOA", "Gone on Arrival"),
    ("UTL", "Unable to Locate"),
    ("UNFOUNDED", "Unfounded"),
    ("REFERRED", "Referred to Other Agency"),
    ("NOACTION", "No Action Required"),
    ("CANCELLED", "Cancelled"),
    ("DUPLICATE", "Duplicate Call"),
    ("TRANSPORT", "Transported"),
]

PRIORITIES = [1, 2, 3, 4, 5]

SOURCES = ["911", "PHONE", "RADIO", "WALK_IN", "OFFICER", "ALARM_CO", "OTHER_AGENCY", "OTHER"]

UNIT_STATUSES = [
    "AVAILABLE", "DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT",
    "BUSY", "OUT_OF_SERVICE", "OFF_DUTY",
]
# Statuses that tie a unit to a call
CALL_STATUSES = {"DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT"}

UNIT_TYPES = ["PATROL", "SUPERVISOR", "DETECTIVE", "K9", "TRAFFIC", "ADMIN", "OTHER_AGENCY", "OTHER"]

PERSON_ROLES = [
    "COMPLAINANT", "CALLER", "VICTIM", "SUSPECT", "WITNESS", "ARRESTEE",
    "DRIVER", "PASSENGER", "OWNER", "SUBJECT", "MISSING", "OTHER",
]

VEHICLE_ROLES = ["INVOLVED", "SUSPECT", "VICTIM", "TOWED", "STOLEN", "RECOVERED", "ABANDONED", "OTHER"]

ROLES = ["admin", "supervisor", "dispatcher", "officer", "records"]
