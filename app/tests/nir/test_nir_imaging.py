from io import BytesIO

import pytest
from pydicom import dcmwrite
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, KeyObjectSelectionDocumentStorage, MRImageStorage

from .imaging import kos_image_request, validate_mr_image


def encode(dataset):
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = dataset.SOPClassUID
    dataset.file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
    output = BytesIO()
    dcmwrite(output, dataset, enforce_file_format=True)
    return output.getvalue()


@pytest.fixture
def kos():
    kos = Dataset()
    kos.SOPClassUID = KeyObjectSelectionDocumentStorage
    kos.SOPInstanceUID = "1.2.3.99"
    kos.HomeCommunityID = "1.2.3"
    kos.RepositoryUniqueID = "1.2.3.10"
    # The manifest's own study/series are deliberately different from its references.
    kos.StudyInstanceUID = "1.2.3.98"
    kos.SeriesInstanceUID = "1.2.3.97"
    study, series, instance = Dataset(), Dataset(), Dataset()
    study.StudyInstanceUID = "1.2.3.1"
    series.SeriesInstanceUID = "1.2.3.2"
    instance.ReferencedSOPInstanceUID = "1.2.3.3"
    instance.ReferencedSOPClassUID = MRImageStorage
    series.ReferencedSOPSequence = [instance]
    study.ReferencedSeriesSequence = [series]
    kos.CurrentRequestedProcedureEvidenceSequence = [study]
    return kos


def test_request_uses_referenced_image_and_explicit_routing(kos):
    request = kos_image_request(encode(kos))
    assert request.study_instance_uid == "1.2.3.1"
    assert request.series[0].series_instance_uid == "1.2.3.2"
    document = request.series[0].documents[0]
    assert document.document_unique_id == "1.2.3.3"
    assert document.home_community_id == "urn:oid:1.2.3"
    assert document.repository_unique_id == "1.2.3.10"


@pytest.mark.parametrize(
    "missing", ["HomeCommunityID", "RepositoryUniqueID", "CurrentRequestedProcedureEvidenceSequence"]
)
def test_missing_routing_or_references_fail(kos, missing):
    delattr(kos, missing)
    with pytest.raises(ValueError):
        kos_image_request(encode(kos))


def test_non_kos_rejected(kos):
    kos.SOPClassUID = MRImageStorage
    with pytest.raises(ValueError, match="Key Object Selection"):
        kos_image_request(encode(kos))


@pytest.mark.parametrize("failure", [None, "identity", "pixels", "modality"])
def test_image_validation(kos, failure):
    request = kos_image_request(encode(kos))
    image = Dataset()
    image.SOPClassUID = MRImageStorage
    image.SOPInstanceUID = "1.2.3.3" if failure != "identity" else "1.2.3.4"
    image.StudyInstanceUID = "1.2.3.1"
    image.SeriesInstanceUID = "1.2.3.2"
    image.Modality = "MR" if failure != "modality" else "CT"
    image.Rows = image.Columns = 2
    image.SamplesPerPixel = 1
    image.PhotometricInterpretation = "MONOCHROME2"
    image.BitsAllocated = image.BitsStored = 16
    image.HighBit = 15
    image.PixelRepresentation = 0
    image.PixelData = b"\x01\x00" * (4 if failure != "pixels" else 1)
    if failure:
        with pytest.raises(ValueError):
            validate_mr_image(encode(image), request)
    else:
        summary = validate_mr_image(encode(image), request)
        assert summary["pixel_shape"] == [2, 2]
        assert summary["sop_instance_uid"] == "1.2.3.3"
