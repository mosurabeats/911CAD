"""Default reference data. Edit these in the Admin screen after install."""

CALL_TYPES = [
    # code, description, default priority (1 = highest), discipline
    ("ALARM", "Alarm - Burglary/Panic", 2, "POLICE"),
    ("ANIMAL", "Animal Complaint", 4, "POLICE"),
    ("ASSAULT", "Assault", 1, "POLICE"),
    ("ASSIST", "Assist Other Agency", 3, "POLICE"),
    ("ASSISTC", "Citizen Assist", 4, "POLICE"),
    ("BURG", "Burglary", 2, "POLICE"),
    ("CHECK", "Building/Area Check", 5, "POLICE"),
    ("CIVIL", "Civil Matter", 4, "POLICE"),
    ("DISORD", "Disorderly Conduct", 2, "POLICE"),
    ("DISTURB", "Disturbance / Noise", 3, "POLICE"),
    ("DOMESTIC", "Domestic Disturbance", 1, "POLICE"),
    ("DUI", "DUI / Impaired Driver", 2, "POLICE"),
    ("FRAUD", "Fraud / Scam", 4, "POLICE"),
    ("HARASS", "Harassment", 3, "POLICE"),
    ("LARCENY", "Larceny / Theft", 3, "POLICE"),
    ("LOST", "Lost / Found Property", 5, "POLICE"),
    ("MENTAL", "Mental Health Crisis", 1, "POLICE"),
    ("MISSING", "Missing Person", 2, "POLICE"),
    ("MVA", "Motor Vehicle Crash", 2, "POLICE"),
    ("MVAINJ", "Motor Vehicle Crash - Injury", 1, "EMS"),
    ("MEDICAL", "Medical Emergency", 1, "EMS"),
    ("OVERDOSE", "Overdose", 1, "EMS"),
    ("PARKING", "Parking Complaint", 5, "POLICE"),
    ("PROPDMG", "Property Damage / Vandalism", 4, "POLICE"),
    ("SERVICE", "Paper Service", 5, "POLICE"),
    ("SUSP", "Suspicious Person/Vehicle", 3, "POLICE"),
    ("THREAT", "Threats", 2, "POLICE"),
    ("TRAFFIC", "Traffic Complaint / Hazard", 3, "POLICE"),
    ("TS", "Traffic Stop", 3, "POLICE"),
    ("TRESPASS", "Trespass", 3, "POLICE"),
    ("WARRANT", "Warrant Service", 3, "POLICE"),
    ("WELFARE", "Welfare Check", 2, "POLICE"),
    ("WEAPON", "Weapons Complaint", 1, "POLICE"),
    ("911HANG", "911 Hang-up / Open Line", 2, "POLICE"),
    ("FIRE", "Fire / Smoke Report", 1, "FIRE"),
    ("OTHER", "Other", 4, "POLICE"),
    # Fire
    ("STRUCT", "Structure Fire", 1, "FIRE"),
    ("FIREALARM", "Fire Alarm Activation", 2, "FIRE"),
    ("CO", "CO Alarm / Detector", 2, "FIRE"),
    ("VEHFIRE", "Vehicle Fire", 2, "FIRE"),
    ("BRUSH", "Brush / Grass Fire", 2, "FIRE"),
    ("GAS", "Gas Leak / Odor", 2, "FIRE"),
    ("HAZMAT", "Hazardous Materials", 1, "FIRE"),
    ("WIRES", "Wires Down / Electrical Hazard", 3, "FIRE"),
    ("EXTRIC", "Extrication / Entrapment", 1, "FIRE"),
    ("WATERRES", "Water / Ice Rescue", 1, "FIRE"),
    ("FIREINV", "Fire Investigation / Smoke Odor", 3, "FIRE"),
    ("MAFIRE", "Mutual Aid - Fire", 2, "FIRE"),
    # EMS
    ("CARDIAC", "Cardiac / Chest Pain", 1, "EMS"),
    ("BREATH", "Breathing Problem", 1, "EMS"),
    ("UNCON", "Unconscious / Unresponsive", 1, "EMS"),
    ("STROKE", "Stroke / CVA", 1, "EMS"),
    ("SEIZURE", "Seizure", 1, "EMS"),
    ("DIABETIC", "Diabetic Problem", 2, "EMS"),
    ("FALL", "Fall", 2, "EMS"),
    ("TRAUMA", "Injury / Trauma", 2, "EMS"),
    ("SICK", "Sick Person", 3, "EMS"),
    ("LIFTASSIST", "Lift Assist", 4, "EMS"),
    ("IFT", "Interfacility Transfer", 4, "EMS"),
    ("MAEMS", "Mutual Aid - EMS", 2, "EMS"),
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
    # Fire / EMS
    ("EXTINGUISHED", "Fire Extinguished"),
    ("FALSEALARM", "False Alarm"),
    ("NOHAZARD", "Investigated - No Hazard Found"),
    ("TREATED", "Treated, Not Transported"),
    ("REFUSAL", "Patient Refused Care / Transport"),
    ("PRONOUNCED", "Pronounced / Deceased"),
    ("MAGIVEN", "Mutual Aid Given"),
]

PRIORITIES = [1, 2, 3, 4, 5]

SOURCES = ["911", "PHONE", "RADIO", "WALK_IN", "OFFICER", "ALARM_CO", "OTHER_AGENCY", "OTHER"]

DISCIPLINES = ["POLICE", "FIRE", "EMS"]

UNIT_STATUSES = [
    "AVAILABLE", "IN_QUARTERS", "DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT", "AT_HOSPITAL",
    "BUSY", "OUT_OF_SERVICE", "OFF_DUTY",
]
# Statuses that tie a unit to a call
CALL_STATUSES = {"DISPATCHED", "ENROUTE", "ONSCENE", "TRANSPORT", "AT_HOSPITAL"}

# unit type -> discipline
UNIT_TYPE_DISCIPLINE = {
    "PATROL": "POLICE", "SUPERVISOR": "POLICE", "DETECTIVE": "POLICE", "K9": "POLICE", "TRAFFIC": "POLICE",
    "ADMIN": "POLICE",
    "ENGINE": "FIRE", "LADDER": "FIRE", "TANKER": "FIRE", "RESCUE": "FIRE", "BRUSH_TRUCK": "FIRE",
    "FIRE_CHIEF": "FIRE", "FIRE_UTILITY": "FIRE",
    "AMBULANCE_ALS": "EMS", "AMBULANCE_BLS": "EMS", "EMS_SUPERVISOR": "EMS", "FLY_CAR": "EMS",
    "OTHER_AGENCY": "POLICE", "OTHER": "POLICE",
}
UNIT_TYPES = list(UNIT_TYPE_DISCIPLINE)

PERSON_ROLES = [
    "COMPLAINANT", "CALLER", "VICTIM", "SUSPECT", "WITNESS", "ARRESTEE",
    "DRIVER", "PASSENGER", "OWNER", "SUBJECT", "MISSING", "PATIENT", "OTHER",
]

VEHICLE_ROLES = ["INVOLVED", "SUSPECT", "VICTIM", "TOWED", "STOLEN", "RECOVERED", "ABANDONED", "OTHER"]

ROLES = ["admin", "supervisor", "dispatcher", "officer", "records"]
