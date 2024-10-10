import json
import logging
import os
from datetime import timedelta
from uuid import uuid4

import httpx
import xmltodict
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fhirclient.models import bundle
from jwcrypto import jwk, jws
from jwcrypto.common import json_encode

from .ccda.convert_mime import convert_mime
from .ccda.fhir2ccda import convert_bundle
from .ccda.helpers import validateNHSnumber
from .gpconnect import *
from .pds import pds
from .redis_connect import redis_client
from .security import create_jwt
from .soap import soap

app = FastAPI()
app.include_router(soap.router)
app.include_router(pds.router)
# app.include_router(gpconnect.router)

REGISTRY_ID = os.getenv("REGISTRY_ID", str(uuid4()))


@app.on_event("startup")
async def startup_event():
    redis_client.set("registry", REGISTRY_ID)
    # check if there is a jwk and if not generate
    if os.path.isfile("keys/jwk.json"):
        pass
    else:
        # generate one with with private key
        with open("keys/test-1.pem", "rb") as pemfile:
            private_pem = pemfile.read()
            public_jwk = jwk.JWK.from_pem(data=private_pem)
            jwk_json = public_jwk.export_public(as_dict=True)
            with open("keys/jwk.json", "w") as f:
                json.dump(jwk_json, f)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    return """
    <html>
        <head>
            <title> Welcome </title
        </head>
        <body>
            <h3>Xhuma</h3>
            <p>This is the internet facing demo for Xhuma</p>
            <p>Interactive API documentation is available <a href="/docs#/">here</a>
            <h4>endpoints</h4>
            <p>/pds/lookuppatient/nhsno will perform a pds lookup and return the fhir response. <a href="pds/lookup_patient/9449306680">Example</a></p>
            <p>/gpconnect/nhsno will perform a gpconnect access record structured query, convert it to a CCDA and return the cached record uuid. <a href="gpconnect/9690937278">Example</a></p>
            <p>for the purposes of the internet facing demo /demo/nhsno will return the mime encoded ccda. <a href="/demo/9690937278">Example</a></p>
        </body>
    </html
    """


@app.get("/demo/{nhsno}")
async def demo(nhsno: int):
    """ """
    bundle_id = await gpconnect(nhsno)

    return redis_client.get(bundle_id["document_id"])


@app.get("/jwk")
async def get_jwk():
    """public endpoint for jwk key"""
    with open("keys/jwk.json", "r") as jwk_file:
        key = json.load(jwk_file)
    return key
