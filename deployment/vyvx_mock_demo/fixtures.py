"""Hand-specified synthetic inputs and independent mock-output oracle, not Oracle parity.

Input defaults fill every XML source column. Output dictionaries below are separately
specified business records, not obtained by executing the transformation under test.
Keys are fixed mock IDs for this finite fixture contract, not production allocation.
"""
from copy import deepcopy

RUN_DATE = "2026-11-07 00:00:00"
TEMPLATE_ID = "990077"
KEYS = {"ECC-A:BAN-A": "9001", "ECC-B:BAN-B": "9002", "ECC-C:BAN-C": "9003",
        "ECC-D:BAN-D": "9004", "ECC-E:BAN-E": "9005", ":BAN-N": "9006",
        "ECC-F:BAN-F": "9007", "ECC-G:BAN-G": "9008"}

def bronze_rows(columns, kind):
    def base():
        return {name: (RUN_DATE if dtype == "TIMESTAMP" else
                       "1" if dtype.startswith("DECIMAL") else "X")
                for name, dtype in columns}
    rows = []
    if kind == "btp":
        common = dict(A_LOC="A", Z_LOC="Z", ASR_TERM="1", BANDWIDTH="10G",
                      ICSC="MOCK", MRC="12.34", NRC="5.67", COMPANY="100",
                      GL_CODE="761999", BILL_DATE=RUN_DATE, EFF_BILL_DATE=RUN_DATE,
                      MUTLI_EC="N", NC="NC", NCI="NCI", PROFIT_CENTER="P",
                      STATUS="ACTIVE", VENDOR="MOCK_VENDOR", VENDOR_NUMBER="V1")
        cases = [
            dict(ECCKT="RAW-A", ECCKT_STRIPPED="ECC-A", BAN="BAN-A", L3_CIRCUIT_ID="L3-A"),
            dict(ECCKT="RAW-A", ECCKT_STRIPPED="ECC-A", BAN="BAN-A", L3_CIRCUIT_ID="L3-A"),
            dict(ECCKT="RAW-B", ECCKT_STRIPPED="ECC-B", BAN="BAN-B", L3_CIRCUIT_ID="l3-b", COMPANY="133", GL_CODE="70000", NRC=None),
            dict(ECCKT="RAW-C", ECCKT_STRIPPED="ECC-C", BAN="BAN-C", L3_CIRCUIT_ID="L3-C"),
            dict(ECCKT="RAW-D", ECCKT_STRIPPED="ECC-D", BAN="BAN-D", L3_CIRCUIT_ID="L3-D", GL_CODE="76104"),
            dict(ECCKT="RAW-E", ECCKT_STRIPPED="ECC-E", BAN="BAN-E", L3_CIRCUIT_ID="L3-E", COMPANY="133", GL_CODE="89999"),
            dict(ECCKT="RAW-F", ECCKT_STRIPPED="ECC-F", BAN="BAN-F", L3_CIRCUIT_ID="L3-F", BILL_DATE="2006-05-27 00:00:00"),
            dict(ECCKT="RAW-G", ECCKT_STRIPPED="ECC-G", BAN="BAN-G", L3_CIRCUIT_ID="L3-G", GL_CODE="70000"),
            dict(ECCKT=None, ECCKT_STRIPPED=None, BAN="BAN-N", L3_CIRCUIT_ID=None, NRC=None),
        ]
        for case in cases:
            rows.append({**base(), **common, **case})
    else:
        common = dict(ASR_A_LOCATION="VA", ASR_Z_LOCATION="VZ", VENDOR_TERM="24",
                      BAN="VBAN", BANDWIDTH="100G", RESERVATION_TYPE="MOCK_CIRCUIT",
                      CUSTOMER_NAME="ECCKT_MATCH", ECCKT="VBUS-A", ECCKT_STRIPPED="ECC-A",
                      L3_CIRCUIT_ID="L3A", MRC="123.45", NRC="5.00", MRC_CUSTOMER="200.10",
                      NRC_CUSTOMER=None, RESERVATION_NAME="DEMO_ORDER", ORDER_TYPE="INSTALL",
                      RESERVATION_ID="R1", STATUS="ACTIVE", VENDOR="MOCK_VBUS",
                      ORDER_STATUS="ACTIVE", ORDER_NUMBER="ORD-A", ORDER_CREATE_DATE="2026-11-01 00:00:00",
                      CURRENCY_INDICATOR="USD", CUSTOMER_TERM="12", PRODUCT_NAME="MOCK_PRODUCT")
        cases = [
            dict(CUSTOMER_NAME="OLD_ORDER", ORDER_CREATE_DATE="2026-10-01 00:00:00", MRC="10.25"),
            {}, {},  # Exact source duplicate removed by SELECT DISTINCT.
            dict(COMMENTS="COMMENT_ONLY_TIE"),  # Distinct SQ row, duplicate mapped output retained.
            dict(CUSTOMER_NAME="CANCEL_SENTINEL", ORDER_STATUS="CANCEL", ORDER_NUMBER="ORD-CANCEL"),
            dict(CUSTOMER_NAME="L3_ONLY_MATCH", ECCKT="VBUS-B", ECCKT_STRIPPED="NO-B",
                 L3_CIRCUIT_ID="L3B", MRC="0.01", NRC=None, ORDER_NUMBER="ORD-B",
                 ORDER_CREATE_DATE="2026-11-02 00:00:00"),
            dict(CUSTOMER_NAME="UNMATCHED_SENTINEL", ECCKT_STRIPPED="NO-Z", L3_CIRCUIT_ID="NOZ"),
            dict(CUSTOMER_NAME="NULL_JOIN_SENTINEL", ECCKT_STRIPPED=None, L3_CIRCUIT_ID=None),
            dict(CUSTOMER_NAME="DELETED_GL_SENTINEL", ECCKT_STRIPPED="ECC-D", L3_CIRCUIT_ID="L3D"),
            dict(CUSTOMER_NAME="L3_PRIORITY_SENTINEL", ECCKT_STRIPPED="NO-A", L3_CIRCUIT_ID="L3A"),
        ]
        for case in cases:
            rows.append({**base(), **common, **case})
    assert len(rows) <= 50
    assert all(set(row) == {name for name, _ in columns} for row in rows)
    return rows

CONTROL_TABLES = {
    "mock_template": {"columns": [("TEMPLATE_ID", "DECIMAL(38,0)"), ("TEMPLATE_NAME", "STRING")],
        "rows": [{"TEMPLATE_ID": TEMPLATE_ID, "TEMPLATE_NAME": "INFA_VBUS_HIST_PREP_PULL"}]},
    "mock_system": {"columns": [("SYSTEM_ID", "DECIMAL(10,0)"), ("SYSTEM_NAME", "STRING")],
        "rows": [{"SYSTEM_ID": "77", "SYSTEM_NAME": "VBUS"}]},
    "mock_product_keys": {"columns": [("SYSTEM_ID", "DECIMAL(10,0)"), ("SRC_SYSTEM_NUM_ID", "STRING"),
        ("RUN_DATE", "TIMESTAMP"), ("MODE_ID", "DECIMAL(10,0)"), ("AE2E_KEY_ID", "DECIMAL(38,0)")],
        "rows": [{"SYSTEM_ID": "77", "SRC_SYSTEM_NUM_ID": k, "RUN_DATE": RUN_DATE,
                  "MODE_ID": "-1", "AE2E_KEY_ID": v} for k, v in KEYS.items()]},
    "mock_dependency": {"columns": [("TEMPLATE_ID", "DECIMAL(38,0)"), ("DEPENDS_ON_TEMPLATE_ID", "DECIMAL(38,0)")],
        "rows": [{"TEMPLATE_ID": TEMPLATE_ID, "DEPENDS_ON_TEMPLATE_ID": "990001"}]},
    "mock_dependency_completion": {"columns": [("TEMPLATE_ID", "DECIMAL(38,0)"), ("RUN_DATE", "TIMESTAMP"), ("END_TIME", "TIMESTAMP")],
        "rows": [{"TEMPLATE_ID": "990001", "RUN_DATE": RUN_DATE, "END_TIME": RUN_DATE}]},
}

# Independently enumerated mapped fields. All other XML target fields are NULL,
# reflecting their unwired XML ports, not guessed data for consumed expressions.
BTP_EXPECTED_BASE = dict(ASR_A_LOC="A", ASR_Z_LOC="Z", ASR_TERM="1", BANDWIDTH="10G",
    ICSC="MOCK", MRC="12.3400", NRC="5.6700", COMPANY_NAME="100", GL_CODE="761999",
    INVOICE_DATE=RUN_DATE, EFFECTIVE_BILL_DATE=RUN_DATE, MULTI_EC="N", NC="NC", NCI="NCI",
    PROFIT_CENTER="P", STATUS="ACTIVE", VENDOR="MOCK_VENDOR", VENDOR_ORDER_NUMBER="V1",
    TEMPLATE_ID=TEMPLATE_ID, PULL_CRITERIA="BTP:ORIGIN")
BTP_EXPECTED = [
    {**BTP_EXPECTED_BASE, "AE2E_KEY_ID": "9001", "ECCKT": "RAW-A", "ECCKT_STRIPPED": "ECC-A", "BAN": "BAN-A", "L3_CIRCUIT_ID": "L3-A"},
    {**BTP_EXPECTED_BASE, "AE2E_KEY_ID": "9001", "ECCKT": "RAW-A", "ECCKT_STRIPPED": "ECC-A", "BAN": "BAN-A", "L3_CIRCUIT_ID": "L3-A"},
    {**BTP_EXPECTED_BASE, "AE2E_KEY_ID": "9002", "ECCKT": "RAW-B", "ECCKT_STRIPPED": "ECC-B", "BAN": "BAN-B", "L3_CIRCUIT_ID": "l3-b", "COMPANY_NAME": "133", "GL_CODE": "70000", "NRC": None},
    {**BTP_EXPECTED_BASE, "AE2E_KEY_ID": "9003", "ECCKT": "RAW-C", "ECCKT_STRIPPED": "ECC-C", "BAN": "BAN-C", "L3_CIRCUIT_ID": "L3-C"},
    {**BTP_EXPECTED_BASE, "AE2E_KEY_ID": "9006", "ECCKT": None, "ECCKT_STRIPPED": None, "BAN": "BAN-N", "L3_CIRCUIT_ID": None, "NRC": None},
]
VBUS_EXPECTED_BASE = dict(AE2E_KEY_ID="9001", TEMPLATE_ID=TEMPLATE_ID,
    ASR_A_LOCATION="VA", ASR_Z_LOCATION="VZ", ASR_TERM="24", BAN="VBAN", BANDWIDTH="100G",
    CIRCUIT_TYPE="MOCK_CIRCUIT", CUSTOMER_NAME="ECCKT_MATCH", ECCKT="VBUS-A", ECCKT_STRIPPED="ECC-A",
    L3_CIRCUIT_ID="L3A", MRC="123.4500", NRC="5.0000", MRC_CUSTOMER="200.10", NRC_CUSTOMER=None,
    ORDER_NAME="DEMO_ORDER", ORDER_TYPE="INSTALL", RESERVATION_ID="R1", STATUS="ACTIVE", VENDOR="MOCK_VBUS",
    PULL_CRITERIA="BTP:ECCKT_STRPD", EVENT_TYPE_DESC="X", ORDER_JEOP="X", ORDER_STATUS="ACTIVE",
    ORDER_NUMBER="ORD-A", ORDER_CREATE_DATE="2026-11-01 00:00:00", CURRENCY_INDICATOR="USD",
    CUSTOMER_REQUESTED_START_DATE=RUN_DATE, INSTALL_DATE=RUN_DATE, DISCONNECT_DATE=RUN_DATE,
    CUSTOMER_BILL_END_DATE="07-NOV-2026", CUSTOMER_BILL_START_DATE="07-NOV-2026",
    CUSTOMER_TERM="12", INVOICE_DATE="07-NOV-2026", PRODUCT_NAME="MOCK_PRODUCT")
VBUS_EXPECTED = [deepcopy(VBUS_EXPECTED_BASE), deepcopy(VBUS_EXPECTED_BASE),
    {**VBUS_EXPECTED_BASE, "AE2E_KEY_ID": "9002", "CUSTOMER_NAME": "L3_ONLY_MATCH",
     "ECCKT": "VBUS-B", "ECCKT_STRIPPED": "NO-B", "L3_CIRCUIT_ID": "L3B",
     "PULL_CRITERIA": "BTP:L3_CKT_ID", "MRC": "0.0100", "NRC": None,
     "ORDER_NUMBER": "ORD-B", "ORDER_CREATE_DATE": "2026-11-02 00:00:00"}]

def expected_rows(columns, kind):
    rows = BTP_EXPECTED if kind == "btp" else VBUS_EXPECTED
    names = {name for name, _ in columns}
    assert all(set(row) <= names for row in rows)
    return [{name: row.get(name) for name, _ in columns} for row in rows]