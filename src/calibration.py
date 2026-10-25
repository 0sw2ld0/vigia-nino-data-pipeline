"""Evaluation on explicitly labelled windows; unlabelled time is never a negative."""
import math


def evaluate_windows(rows, thresholds=(2,5,10,15,20,30,40)):
    """Rows: split(train/validation), event_id, observed_event(bool), rain_max_mmh.

    A row represents a predeclared independent event/negative window, not every scan.
    This reports validation metrics; it never promotes thresholds automatically.
    """
    training_ids = {r['event_id'] for r in rows if r['split']=='train'}
    validation = [r for r in rows if r['split']=='validation']
    if training_ids & {r['event_id'] for r in validation}:
        raise ValueError('Event leakage between train and validation')
    usable = []
    for row in validation:
        if type(row['observed_event']) is not bool:
            raise ValueError('Only explicitly reviewed positive/negative labels accepted')
        value = row.get('rain_max_mmh')
        if value is not None and math.isfinite(value) and value >= 0:
            usable.append(row)
    positive = sum(r['observed_event'] for r in usable)
    negative = len(usable)-positive
    metrics = []
    for threshold in thresholds:
        tp=fp=tn=fn=0
        for row in usable:
            alert = row['rain_max_mmh'] >= threshold
            if alert and row['observed_event']: tp += 1
            elif alert: fp += 1
            elif row['observed_event']: fn += 1
            else: tn += 1
        metrics.append({'threshold_mmh':threshold,'tp':tp,'fp':fp,'tn':tn,'fn':fn,
                        'recall':tp/(tp+fn) if tp+fn else None,
                        'precision':tp/(tp+fp) if tp+fp else None,
                        'false_positive_rate':fp/(fp+tn) if fp+tn else None})
    return {'state':'INSUFFICIENT_EVIDENCE' if not positive or not negative else 'EVALUATED_NOT_OPERATIONALLY_VALIDATED',
            'validation_positive_windows':positive,'validation_negative_windows':negative,
            'missing_rain_windows':len(validation)-len(usable),'metrics':metrics,
            'selected_threshold':None,'limitation':'No automatic threshold selection. Independent events, station bias checks, spatial representativeness and uncertainty review still required.'}
