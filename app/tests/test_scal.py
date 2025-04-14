"""
Script to run through consumer tests for GP CONNECT Scal
"""

import asyncio

from app.gpconnect import gpconnect


def test_GPC_STR_TST_GEN_02(self):
    """Given I have imported GP Connect data
    And I support data sharing with other systems
    When I receive a request for patient record data for a patient I hold GP Connect data for
    Then I only include GP Connect data where the request is for Direct Care use only
    And I always include the resource identifiers received from GP Connect messages when exporting the data, adding any other identifiers as appropriate
    """
    pass


def test_GPC_STR_TST_GEN_05(self):
    """Given I am at a point in the system where I have access to attempt a call to a GP Connect service
    When I make that attempt to access GP Connect
    Then an audit record is written to an appropriate auidit log including when access is blocked, unsuccessful or successful
    And the audit record confirms to NHS Digital audit standards
    """
    nhsnos = ["9658218873", "9658220142", "9476718943"]
    pass


def test_GPC_STR_TST_GEN_06(self):
    """Given I have access to request data from GP Connect and the patient trace was [time] ago
    When I make that attempt to access GP Connect
    Then the GP Connect is request message is [result]
    Examples result: blocked, time: > 24 hours; result sent, time: < 24 hours
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_07(self):
    """Given I have made a successful request to GP Connect
    When I receive a valid response including a patient resource
    Then I verify the patient resource details for family name, given name, gender, date of birth and GP Practice Code match to those presented to the user from the local system in the patient record
    And I alert the user to any mismatch between the local record demographics and those provided in the GP Connect response message
    """
    nhsno = "9658218873"
    # this is satisfied by the iti47 or 55 request prior to a gp connect request
    pass


def test_GPC_STR_TST_GEN_08(self):
    """Given I have access to request data from GP Connect and the patient trace was within the last 24 hours
    When I make that attempt to access GP Connect
    Then the registered GP practice from the last PDS trace is used to identify the practice to submit the request to
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_09(self):
    """Given I have access to request data from GP Connect but I cannot confirm the registered practice either because it is not on PDS or the patient has an s-flag
    When I attempt to access GP Connect
    Then the request to GP Connect is blocked and handled gracefully so the user is aware that access is not available for that patient at that time
    """
    nhsnos = ["9658220142", "9658220150"]

    for nhsno in nhsnos:
        response = asyncio.run(gpconnect(nhsno))
        assert response.status_code == 403


def test_GPC_STR_TST_GEN_10(self):
    """Given I access a patient which is recorded as deceased on PDS or on the local system
    When I am at a point where I would normally be able to access GP Connect
    Then the system prevents access to GP Connect
    And handles the prevention gracefully so the users is aware that GP Connect is not available for this patient
    """
    nhsno = "9658220290"

    response = asyncio.run(gpconnect(nhsno))
    assert response.status_code == 403


def test_GPC_STR_TST_GEN_11(self):
    """Given I have made a request to a GP Connect service
    When I receive a aptient not found error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsno = "9999999999"
    response = asyncio.run(gpconnect(nhsno))
    assert response.status_code == 404


def test_GPC_STR_TST_GEN_12(self):
    """Given I have made a request to a GP Connect service
    When I receive a patient dissent to share error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsno = "9658220169"
    response = asyncio.run(gpconnect(nhsno))
    assert response.status_code == 403


def test_GPC_STR_TST_GEN_13(self):
    """Given I have made a request to a GP Connect service using an Invalid Resource (The Parameters resource passed does not conform to that specified in the GPConnect-GetStructuredRecord-Operation-1 OperationDefinition)
    When I receive an invalid resource error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_14(self):
    """Given I have made a request to a GP Connect service using an Invalid NHS Number
    When I receive an invalid NHS number error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsno = "965821883"
    response = asyncio.run(gpconnect(nhsno))
    assert response.status_code == 400

    nhsno = "testing"
    response = asyncio.run(gpconnect(nhsno))
    assert response.status_code == 400


def test_GPC_STR_TST_GEN_15(self):
    """Given I have made a request for allergies to a GP Connect service with invalid Allergies Parameters/Part Parameters
    When I receive an invalid parameter error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_16(self):
    """Given I have made a request for medications to a GP Connect service invalid Medications Parametes/Part Parameters
    When I receive an invalid parameter error response
    Then I handle the response gracefully
    And I make available all the diagnostic details to appropriate people to enable fault resolution
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_17(self):
    """Given I have sent a valid message to GP Connect
    When I receive a response including a data in transit warning
    Then I make the user aware as appropriate
    """
    nhsno = "9658219705"
    pass


def test_GPC_STR_TST_GEN_18(self):
    """Given I have sent a valid message to GP Connect
    And I have requested allergies are included
    When I receive a response including a confidential items warning for allergies
    Then I make the user aware and apply controls as appropriate
    """
    nhsno = "9658219705"
    pass


def test_GPC_STR_TST_GEN_19(self):
    """Given I have sent a valid message to GP Connect
    And I have requested medications are included
    When I receive a response including a confidential items warning for medications
    Then I make the user aware and apply controls as appropriate
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_20(self):
    """Given I have received a valid message response
    When I present the data to the end user
    Then the user is aware that the data has come from the patient's registered GP record (this may be expressed generically or specific to the source practice)
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_24(self):
    """Given I have received a successful, valid response message
    When I import the GP Connect resource / data into the local system
    Then I always retain resource identifiers including, but not limited to, system and value
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_GEN_25(self):
    """Given I have sent a valid message to GP Connect
    And I have included a request for medications data
    When I receive a response including a data in transit warning and a confidential data items warning for medications
    Then I make the user aware as appropriate and that the data in transit warning is shown as applicable to all data
    And the confidential data warning is shown to apply to medications data only
    """
    nhsno = "9658219705"
    pass


def test_GPC_STR_TST_MED_01(self):
    """Given I am enabled to access GP Connect data for a given patient
    And I want to retrieve a full medication history
    When I make the medication request to GP Connect
    Then the request conforms to the GP Connect specification
    And includes the patient's NHS Number
    And the request has the includeMedication parameter
    And the request sets the includePrescriptionIssues part parameter to true
    And the request does NOT include the medicationSearchFromDate parameter
    And the resulting response is processed successfully by the Consumer
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_MED_02(self):
    """Given I have received a successful, valid medications message response
    When I display or use the medication information
    Then I display or utilise all the key information to represent or process the medication record(s) commenserate with the original record meaning and my specific use case
    """
    nhsno = "9658218873"
    # test fhir response marries up to ccda sections
    pass


def test_GPC_STR_TST_MED_03(self):
    """Given I am enabled to access GP Connect data for a given patient
    And I want to retrieve medication details but I do not require a full medication history
    When I make the medication request to GP Connect
    Then the request conforms to the GP Connect specification
    And includes the patient's NHS Number
    And the request has the includeMedication parameter
    And the request sets the includePrescriptionIssues part parameter to true or false
    And the request includes the medicationSearchFromDate parameter
    And the medicationSearchFromDate is in the defined format
    And the medicationSearchFromDate is equal or less than the current date
    And the resulting response is processed successfully by the Consumer
    """
    nhsno = "9658218873"
    # may be N/a?
    pass


def test_GPC_STR_TST_MED_04(self):
    """Given I am enabled to access GP Connect data for a given patient
    And I am able to specify the date from which I want medications
    When I attempt to request medications by a future date
    Then I am prevented from submitting the request
    """
    nhsno = "9658218873"
    # N/A user not able to select date
    pass


def test_GPC_STR_TST_MED_05(self):
    """Given I am enabled to access GP Connect data for a given patient
    And my use case [inc issue] require medication issues to be included
    When I make the medication request to GP Connect
    Then the request conforms to the GP Connect specification
    And includes the patient's NHS Number
    And the request has the includeMedication parameter
    And the request sets the includePrescriptionIssues part parameter to [param value]
    And the resulting response is processed successfully by the Consumer
    Examples: inc Issues: does, does not; param value: true, false
    """
    nhsno = "9658218873"
    # N/A user not able to select issues
    pass


def test_GPC_STR_TST_MED_07(self):
    """Given I have received a successful, valid medications message response
    And the response has a list with an empty reason
    And the response does not include medication resourcese
    When I display or use the medication information
    Then I display or utilise the list empty reson to inform the user that the patient has no medication records within the request parameters in a way appropriate to my use case
    """
    nhsno = "9658218903"
    # check ccda shows no information
    pass


def test_GPC_STR_TST_MED_08(self):
    """Given I am enabled to access GP Connect data for a given patient
    And I want to retrieve a full medication history
    When I make the medication request to GP Connect
    Then the request conforms to the GP Connect specification
    And includes the patient's NHS Number
    And the request has the includeMedication parameter
    And the request does NOT include the includePrescriptionIssues part parameter OR includes and sets the includePrescriptionIssues part parameter to true
    And the request does NOT include the medicationSearchFromDate parameter
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_ALG_01(self):
    """Given the user wishes to view / import all current allergies OR the system is set to only view / import all current allergies
    When the user selects to access current allergies from GP Connect
    Then the resulting request is populated with valid syntax using the includeAllergies parameter with part parameter includeResolvedAllergies set to false
    And the resulting response is processed successfully by the Consumer.
    """
    nhsno = "9658218873"
    # we always ask for allergies do check they come through
    pass


def test_GPC_STR_TST_ALG_02(self):
    """Given the user wishes to view / import all allergies, including resolved allergies OR the system is set to only view / import all allergies, including resolved allergies
    When the user selects to access all allergies from GP Connect
    Then the resulting request is populated with valid syntax using the includeAllergies parameter with part parameter includeResolvedAllergies set to true
    And the resulting response is processed successfully by the Consumer.
    """
    nhsno = "9658218873"
    # ? N/A as we don't ask for resolved currently
    pass


def test_GPC_STR_TST_ALG_03(self):
    """Given I have received a successful, valid allergies message response
    And the response includes resolved allergies
    When I display or use the allergies information
    Then my system identifies the resolved allergies and handles them in a clinical safe manner such that they remain distinct from current allergies
    And where the resolved allergies are presented in the UI they are clearly and prominently labelled as ended, resolved or equivalent
    And ensures that the resolved allergies cannot be utilised by decision support (where decision support is in use)
    """
    nhsno = "9658218873"
    pass


def test_GPC_STR_TST_ALG_04(self):
    """Given I have received a successful, valid allergies message response
    When I display or use the allergy information
    Then I display or utilise all the key information to represent or process the allergy record(s) commenserate with the original record meaning and my specific use case
    """
    nhsno = "9658218873"
    # check CCDA allergy section
    pass


def test_GPC_STR_TST_ALG_05(self):
    """Given I have received a successful, valid allergies message response
    And the response includes allergies which is not recognised by my system
    When I display or use the allergy information
    Then I display or utilise any SNOMED code or alternative code system coding, as applicable to my use case
    And I display or utilise the allergy name as provided which represents the name of the allergy as entered by the original user, as applicable to my use case
    And I can handle any records which are sent as allergies but are not recognised as allergy codes by my system
    And if the unrecognised record is stored it is degraded
    """
    # check snomed codes are on all allergies
    pass  # Need discussion if this test and requirement is applicable


def test_GPC_STR_TST_ALG_07(self):
    """Given I have received a successful, valid allergies message response
    And the response includes an empty active allergies list resource indicating that the patient record has no content recorded
    When I display or use the allergies response
    Then I recognise this as a record with no active allergies recorded
    And I handle it appropriate to my use case and in such a way it is not confused with a clinical assertion of no known allergies
    """
    nhsno = "9658218865"
    #  check ccda allergy for no information
    pass


def test_GPC_STR_TST_ALG_08(self):
    """Given I have received a successful, valid allergies message response
    And the response includes a single code item which indicates that the clinician has recorded that the patient has no known allergies
    When I display or use the allergies response
    Then I recognise this as a clinical assertion of no known allergies
    And I handle it appropriate to my use case and in such a way it is not confused with an empty list response
    """
    nhsno = "9658218989"
    pass
