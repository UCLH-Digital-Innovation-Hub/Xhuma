"""DICOM checks for the published Q12 sandbox scenario, not a production KOS decoder."""

from io import BytesIO

from pydicom import dcmread
from pydicom.uid import KeyObjectSelectionDocumentStorage, MRImageStorage

from app.soap.nir_models import NIRDocumentReference, NIRImagingSeriesRequest, NIRImagingStudyRequest


def kos_image_request(content: bytes):
    """Select one MR reference, using the sandbox KOS's explicit XDS routing fields."""
    kos = dcmread(BytesIO(content))
    if kos.SOPClassUID != KeyObjectSelectionDocumentStorage:
        raise ValueError("Expected a DICOM Key Object Selection manifest")
    # Do not substitute the ITI-39 manifest repository for the image repository.
    home = str(kos.get("HomeCommunityID", ""))
    repository = str(kos.get("RepositoryUniqueID", ""))
    if not home or not repository:
        raise ValueError("Sandbox KOS must provide HomeCommunityID and RepositoryUniqueID")
    home = home if home.startswith("urn:oid:") else f"urn:oid:{home}"
    for study in kos.get("CurrentRequestedProcedureEvidenceSequence", []):
        for series in study.get("ReferencedSeriesSequence", []):
            for instance in series.get("ReferencedSOPSequence", []):
                if instance.ReferencedSOPClassUID != MRImageStorage:
                    continue
                reference = NIRDocumentReference(
                    home_community_id=home,
                    repository_unique_id=repository,
                    document_unique_id=str(instance.ReferencedSOPInstanceUID),
                    mime_type="application/dicom",
                )
                request = NIRImagingStudyRequest(
                    study_instance_uid=str(study.StudyInstanceUID),
                    series=[
                        NIRImagingSeriesRequest(
                            series_instance_uid=str(series.SeriesInstanceUID), documents=[reference]
                        )
                    ],
                )
                return request
    raise ValueError("KOS contains no referenced MR image")


def validate_mr_image(content: bytes, request: NIRImagingStudyRequest) -> dict:
    """Require matching DICOM identity and decodable pixels; this does not prove display."""
    image = dcmread(BytesIO(content))
    series = request.series[0]
    expected = (request.study_instance_uid, series.series_instance_uid, series.documents[0].document_unique_id)
    actual = (str(image.StudyInstanceUID), str(image.SeriesInstanceUID), str(image.SOPInstanceUID))
    if actual != expected:
        raise ValueError("Retrieved image identifiers do not match the KOS reference")
    if image.SOPClassUID != MRImageStorage or image.Modality != "MR":
        raise ValueError("Expected an MR Image Storage object")
    if (
        image.file_meta.MediaStorageSOPInstanceUID != image.SOPInstanceUID
        or image.file_meta.MediaStorageSOPClassUID != image.SOPClassUID
    ):
        raise ValueError("DICOM file metadata disagrees with the image identity")
    pixels = image.pixel_array
    if not pixels.size or int(image.Rows) <= 0 or int(image.Columns) <= 0:
        raise ValueError("Retrieved image has no pixels")
    return {
        "study_instance_uid": actual[0],
        "series_instance_uid": actual[1],
        "sop_instance_uid": actual[2],
        "sop_class_uid": str(image.SOPClassUID),
        "modality": str(image.Modality),
        "pixel_shape": list(pixels.shape),
        "pixel_dtype": str(pixels.dtype),
        "byte_length": len(content),
    }
