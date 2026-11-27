"""Models for the SAML structure Xhuma generates, using our SOAP XML convention.

Verified NameID, authentication and attribute elements remain XML fragments.
Their namespaces and extension content must survive reissuance. They are never
parsed into these models before signature verification, and signed XML never
passes back through a model serializer.
"""

from copy import deepcopy
from uuid import uuid4

import xmltodict
from lxml import etree as ET
from pydantic import Field

from app.soap.models import XmlModel

SAML = "urn:oasis:names:tc:SAML:2.0:assertion"
WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
DS = "http://www.w3.org/2000/09/xmldsig#"


class BearerConfirmationData(XmlModel):
    not_on_or_after: str = Field(alias="@NotOnOrAfter")


class BearerConfirmation(XmlModel):
    method: str = Field(default="urn:oasis:names:tc:SAML:2.0:cm:bearer", alias="@Method")
    data: BearerConfirmationData = Field(alias="saml:SubjectConfirmationData")


class SAMLSubject(XmlModel):
    # The verified NameID may include Format and issuer qualifiers. Copy the
    # original element rather than reconstructing it from the audit user's ID.
    name_id: ET._Element = Field(exclude=True)
    confirmation: BearerConfirmation = Field(alias="saml:SubjectConfirmation")


class AudienceRestriction(XmlModel):
    audience: str = Field(alias="saml:Audience")


class SAMLConditions(XmlModel):
    not_before: str = Field(alias="@NotBefore")
    not_on_or_after: str = Field(alias="@NotOnOrAfter")
    restriction: AudienceRestriction = Field(alias="saml:AudienceRestriction")


class SignaturePlaceholder(XmlModel):
    # SignXML replaces this marker in the SAML schema's signature position.
    identifier: str = Field(default="placeholder", alias="@Id")


class SAMLAssertion(XmlModel):
    xmlns_saml: str = Field(default=SAML, alias="@xmlns:saml")
    xmlns_ds: str = Field(default=DS, alias="@xmlns:ds")
    identifier: str = Field(default_factory=lambda: "_" + uuid4().hex, alias="@ID")
    version: str = Field(default="2.0", alias="@Version")
    issue_instant: str = Field(alias="@IssueInstant")
    issuer: str = Field(alias="saml:Issuer")
    signature: SignaturePlaceholder = Field(default_factory=SignaturePlaceholder, alias="ds:Signature")
    subject: SAMLSubject = Field(alias="saml:Subject")
    conditions: SAMLConditions = Field(alias="saml:Conditions")
    # Issuance is not a new login. Keep the signed authentication event and
    # structured claims verbatim, including unfamiliar namespace-qualified data.
    authentication: ET._Element = Field(exclude=True)
    attributes: ET._Element = Field(exclude=True)


class WSSecurity(XmlModel):
    xmlns_wsse: str = Field(default=WSSE, alias="@xmlns:wsse")
    assertion: SAMLAssertion = Field(alias="saml:Assertion")

    def to_element(self) -> ET._Element:
        """Serialize generated fields, then insert verified XML before signing.

        This is the only model/XML assembly boundary. Explicit insertion keeps
        SAML child ordering and avoids dictionary round-trips of signed claims.
        The caller attaches this header to its final envelope before signing.
        """
        content = xmltodict.unparse({"wsse:Security": self.to_xml_dict()}, pretty=False).encode("utf-8")
        security = ET.fromstring(content, ET.XMLParser(resolve_entities=False, no_network=True))
        assertion = security.find(f"{{{SAML}}}Assertion")
        assertion.find(f"{{{SAML}}}Subject").insert(0, deepcopy(self.assertion.subject.name_id))
        assertion.append(deepcopy(self.assertion.authentication))
        assertion.append(deepcopy(self.assertion.attributes))
        return security
