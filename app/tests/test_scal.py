"""
Script to run through consumer tests for GP CONNECT Scal
"""

import pytest

from app.gpconnect import gpconnect

from .log_context import capture_test_logs

audit_dict = {
    "subject_id": "Test Subject",
    "organization": "Test Org",
    "organization_id": "urn:oid:test-org-id",
    "home_community_id": "urn:oid:test-home-id",
    "role": {
        "Role": {
            "@codeSystem": "2.16.840.1.113883.6.96",
            "@code": "224608005",
            "@displayName": "Admin",
            "@xmlns": "urn:hl7-org:v3",
        }
    },
    "purpose_of_use": {
        "PurposeForUse": {
            "@xsi:type": "CE",
            "@code": "TREATMENT",
            "@codeSystem": "2.16.840.1.113883.3.18.7.1",
            "@displayName": "Treatment",
            "@xmlns": "urn:hl7-org:v3",
        },
    },
    "resource_id": "test-resource-id",
}


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_05():
    """GPC-STR-TST-GEN-05"""
    nhsnos = ["9690937286", "9690938533"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-05", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_GEN_06():
    """PC-STR-TST-GEN-06"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-GEN-06", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_07():
    """GPC-STR-TST-GEN-07"""
    nhsnos = []
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-07", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_GEN_08():
    """PC-STR-TST-GEN-08"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-GEN-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_09():
    """GPC-STR-TST-GEN-09"""
    nhsnos = ["9690938533", "9690938541"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-09", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_10():
    """GPC-STR-TST-GEN-10"""
    nhsnos = ["9690938681"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-10", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_11():
    """GPC-STR-TST-GEN-11"""
    nhsnos = ["9999999999"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-11", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_12():
    """GPC-STR-TST-GEN-12"""
    nhsnos = ["9690938576"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-12", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_13():
    """GPC-STR-TST-GEN-13"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-13", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_14():
    """GPC-STR-TST-GEN-14"""
    nhsnos = []
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-14", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_17():
    """GPC-STR-TST-GEN-17"""
    nhsnos = ["9690938096"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-17", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_GEN_20():
    """PC-STR-TST-GEN-20"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-GEN-20", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_MED_02():
    """GPC-STR-TST-MED-02"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-MED-02", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_MED_07():
    """GPC-STR-TST-MED-07"""
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-MED-07", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_ALG_01():
    """GPC-STR-TST-ALG-01"""
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-ALG-01", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_ALG_07():
    """GPC-STR-TST-ALG-07"""
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-ALG-07", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_ALG_08():
    """GPC-STR-TST-ALG-08"""
    nhsnos = ["9690937375"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-ALG-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_15():
    """GPC-STR-TST-GEN-15"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-15", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_16():
    """GPC-STR-TST-GEN-16"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-16", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_18():
    """GPC-STR-TST-GEN-18"""
    nhsnos = ["9690938118"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-18", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_INV_07():
    """GPC-STR-TST-INV-07"""
    nhsnos = ["9690937294"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-INV-07", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_INV_02():
    """GPC-STR-TST-INV-02"""
    nhsnos = ["9690937294"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-INV-02", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_INV_05():
    """PC-STR-TST-INV-05"""
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-INV-05", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_INV_06():
    """GPC-STR-TST-INV-06"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-INV-06", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_PRB_01():
    """PC-STR-TST-PRB-01"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-PRB-01", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_PRB_04():
    """PC-STR-TST-PRB-04"""
    nhsnos = ["9690937308"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-PRB-04", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_PRB_05():
    """PC-STR-TST-PRB-05"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-PRB-05", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_PRB_08():
    """GPC-STR-TST-PRB-08"""
    nhsnos = ["9658218873"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-PRB-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_IMM_01():
    """GPC-STR-TST-IMM-01"""
    nhsnos = ["9690938207"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-IMM-01", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_IMM_03():
    """GPC-STR-TST-IMM-03"""
    nhsnos = ["9690938207"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-IMM-03", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_IMM_05():
    """GPC-STR-TST-IMM-05"""
    nhsnos = ["965821890"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-IMM-05", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_IMM_06():
    """GPC-STR-TST-IMM-06"""
    nhsnos = ["9690938207"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-IMM-06", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_IMM_08():
    """GPC-STR-TST-IMM-08"""
    nhsnos = ["9658218873"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-IMM-08", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_PC_STR_TST_GEN_15():
    """PC-STR-TST-GEN-15"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("PC-STR-TST-GEN-15", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result


@pytest.mark.asyncio
async def test_GPC_STR_TST_GEN_16():
    """GPC-STR-TST-GEN-16"""
    nhsnos = ["9690937286"]
    for nhsno in nhsnos:
        async with capture_test_logs("GPC-STR-TST-GEN-16", nhsno) as log_dir:
            result = await gpconnect(nhsno, saml_attrs=audit_dict, log_dir=log_dir)
            assert "document_id" in result
