"""Empty schema fixture contracts, no provider or live database calls."""
import pytest

from workbench.source_fixtures import identifier, mock_type, plan_sources, table_sql


@pytest.mark.parametrize("kind,db,precision,scale,expected", [
    ("date", "Oracle", "19", "0", "TIMESTAMP_NTZ"),
    ("long", "Oracle", "4000", "0", "STRING"),
    ("tinyint", "Sybase", "3", "0", "SMALLINT"),
    ("number(p,s)", "Oracle", "38", "13", "DECIMAL(38,13)"),
    ("money", "Microsoft SQL Server", "19", "4", "DECIMAL(19,4)"),
    ("raw", "Oracle", "1", "0", "BINARY"),
    ("float", "Microsoft SQL Server", "15", "0", "DOUBLE"),
])
def test_vendor_type_semantics(kind, db, precision, scale, expected):
    assert mock_type({"DATATYPE": kind, "PRECISION": precision, "SCALE": scale}, db) == expected


def test_fail_closed_types_and_identifiers():
    with pytest.raises(ValueError):
        mock_type({"DATATYPE": "mystery"}, "Oracle")
    with pytest.raises(ValueError):
        mock_type({"DATATYPE": "decimal", "PRECISION": "39", "SCALE": "0"}, "Oracle")
    with pytest.raises(ValueError):
        identifier("anything`; DROP TABLE something")


def test_namespaces_columns_and_additive_ddl(tmp_path):
    path = tmp_path / "schema.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <SOURCE NAME="LOG" DATABASETYPE="Oracle" DBDNAME="A" OWNERNAME="OWNER">
        <SOURCEFIELD NAME="WHEN" DATATYPE="date" PRECISION="19" SCALE="0" NULLABLE="NOTNULL"/>
      </SOURCE>
      <SOURCE NAME="LOG" DATABASETYPE="Oracle" DBDNAME="B" OWNERNAME="OWNER">
        <SOURCEFIELD NAME="COUNTER" DATATYPE="number" PRECISION="10" SCALE="0" NULLABLE="NULL"/>
      </SOURCE>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    plan = plan_sources(path, "fixture_test")
    assert plan["table_count"] == 2 and plan["column_count"] == 2
    assert plan["schema_count"] == 2
    assert plan == plan_sources(path, "fixture_test")
    sql = table_sql(plan, plan["tables"][0])
    assert "`WHEN` TIMESTAMP_NTZ NOT NULL" in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "DROP" not in sql and "INSERT" not in sql and "TRUNCATE" not in sql
    assert all(t["binding_status"] == "test_fixture_only_not_approved_source_binding" for t in plan["tables"])