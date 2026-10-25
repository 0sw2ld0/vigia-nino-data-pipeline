from pathlib import Path
import pytest
from shapely.geometry import box, mapping
import watersheds
from calibration import evaluate_windows


def feature(code,x0,x1):
    return {'type':'Feature','properties':{'CODIGO':code,'NOMBRE':code,'AREA_KM2':10},
            'geometry':mapping(box(x0,-12.1,x1,-11.9))}


def test_assignment_requires_length_coverage_and_rejects_ambiguous():
    channels = [[1,'name',[[[-77,-12],[-76.9,-12]]]]]
    first = feature('1',-77.1,-76.8)
    assert watersheds.assign_units(channels,[first])['IGN-1']['unit_id']=='ANA-1'
    assert watersheds.assign_units(channels,[first,feature('2',-77.1,-76.8)])=={}
    assert watersheds.assign_units(channels,[feature('3',-77.1,-76.98)])=={}


def test_map_is_reference_polygon_not_exposure():
    output = watersheds.map_features([feature('1',-77.1,-76.8)],'2026-10-25T00:00:00Z')
    assert output[0]['geometry']['type']=='Polygon'
    assert output[0]['properties']['kind']=='BASIN'
    assert output[0]['properties']['review_status']=='REFERENCE'


def test_real_catalog_checksum_and_count():
    root = Path(__file__).resolve().parents[1]
    features,assignments,report = watersheds.load_catalog(root)
    assert len(features)==30
    assert len(assignments)>10000
    assert report['unassigned_segments']+report['assigned_segments']==12462
    assert all(.9<=a['channel_coverage_fraction']<=1 for a in assignments.values())


def test_calibration_missing_labels_is_not_calibrated():
    result = evaluate_windows([])
    assert result['state']=='INSUFFICIENT_EVIDENCE'
    assert result['selected_threshold'] is None
    assert result['metrics'][0]['precision'] is None


def test_evaluation_uses_independent_explicit_negative_windows():
    rows = [dict(split='validation',event_id='event1',observed_event=True,rain_max_mmh=10),
            dict(split='validation',event_id='reviewed-no-event',observed_event=False,rain_max_mmh=3),
            dict(split='validation',event_id='missing',observed_event=False,rain_max_mmh=None)]
    result = evaluate_windows(rows,[5])
    assert result['metrics'][0]['tp']==1
    assert result['metrics'][0]['tn']==1
    assert result['missing_rain_windows']==1
    assert result['state']=='EVALUATED_NOT_OPERATIONALLY_VALIDATED'
    with pytest.raises(ValueError,match='leakage'):
        evaluate_windows(rows+[dict(rows[0],split='train')])
