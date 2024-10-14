import pprint
from xml.etree import ElementTree

import requests
import xmltodict
from xmlschema import XMLSchema

if __name__ == "__main__":

    with open("xml/pdqRequest.xml") as iti47:
        response_schema = XMLSchema("xml/multicacheschemas/PRPA_IN201306UV02.xsd")

        print("ITI47 query")

        dom = ElementTree.parse(iti47)
        root = dom.getroot()
        body = ElementTree.tostring(root)

        headers = {"Content-Type": "application/soap+xml"}
        url = "http://localhost:8000/SOAP/iti47"

        r = requests.post(url, data=body, headers=headers)
        print(r.status_code)

        with open("iti47.xml", "w") as output:
            output.write(r.text)

        print("-" * 20)

    docid = ""

    with open("xml/2. Perform XCA ITI-38 query.xml") as iti38:

        print("ITI38 query")

        dom = ElementTree.parse(iti38)
        root = dom.getroot()
        body = ElementTree.tostring(root)
        headers = {"Content-Type": "application/soap+xml"}
        url = "http://localhost:8000/SOAP/iti38"
        r = requests.post(url, data=body, headers=headers)

        dict_38 = xmltodict.parse(r.text)
        pprint.pprint(dict_38)
        # docid = dict_38["s:Envelope"]["s:Body"]["AdhocQueryResponse"][
        #     "RegistryObjectList"
        # ]["ExtrinsicObject"]["ExternalIdentifier"]["@value"]
        with open("iti38.xml", "w") as output:
            output.write(r.text)
        print(r.status_code)

        print("-" * 20)

    with open("xml/4. Perform XCA ITI-39 document retrieve.xml") as iti39:
        print("ITI39")

        dom = ElementTree.parse(iti39)
        root = dom.getroot()
        xmldict = xmltodict.parse(
            ElementTree.tostring(root),
            process_namespaces=True,
            namespaces={
                "http://www.w3.org/2003/05/soap-envelope": None,
                "http://www.w3.org/2005/08/addressing": None,
                "urn:oasis:names:tc:ebxml-regrep:xsd:query:3.0": None,
                "urn:oasis:names:tc:ebxml-regrep:xsd:rim:3.0": None,
                "urn:ihe:iti:xds-b:2007": None,
            },
        )
        # pprint.pprint(xmldict)
        query = xmldict["Envelope"]
        query["Body"]["RetrieveDocumentSetRequest"]["DocumentRequest"][
            "DocumentUniqueId"
        ] = docid
        pprint.pprint(query)

        # body = ElementTree.tostring(root)
        body = {"Envelope": query}
        body = xmltodict.unparse(body)
        headers = {"Content-Type": "application/soap+xml"}

        url = "http://127.0.0.1:8000/SOAP/iti39"
        r = requests.post(url, data=body, headers=headers)

        # save iti39 response
        with open("iti39.xml", "w") as output:
            output.write(r.text)
        print(r.status_code)
