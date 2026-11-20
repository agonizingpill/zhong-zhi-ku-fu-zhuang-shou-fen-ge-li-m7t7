"""封口失败风险：袋材 + 在田天数 + 历次检查次数的确定性逻辑回归。

只用标准库实现（带岭正则的牛顿法），便于在请求内拟合、在证据里完整复现系数。
样本不足或历史结果单一类别时不拟合，业务上要求先复检，而不是编造概率。
"""
import hashlib
import json
import math

MATERIALS = ('paper', 'nonwoven', 'nylon')
MIN_SAMPLES = 12
REFERENCE = 0.40  # 仅作图上参考线，不作为自动换袋判据；任何不合格都入队
FEATURES = ('intercept', 'material:nonwoven', 'material:nylon', 'months_in_field', 'prior_checks')
# 截距只做极轻正则，其余特征用岭正则避免小样本完全分离。
RIDGE = (0.01, 0.5, 0.5, 0.5, 0.5)


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def vector(material, days, prior):
    return [
        1.0,
        1.0 if material == 'nonwoven' else 0.0,
        1.0 if material == 'nylon' else 0.0,
        max(0.0, float(days)) / 30.0,
        float(max(0, prior)),
    ]


def _solve(matrix, values):
    """带部分主元的高斯消元，规模固定为 5x5。"""
    n = len(values)
    rows = [list(matrix[i]) + [values[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise ValueError('singular')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        lead = rows[col][col]
        for r in range(col + 1, n):
            factor = rows[r][col] / lead
            if factor:
                for c in range(col, n + 1):
                    rows[r][c] -= factor * rows[col][c]
    answer = [0.0] * n
    for i in range(n - 1, -1, -1):
        answer[i] = (rows[i][n] - sum(rows[i][j] * answer[j] for j in range(i + 1, n))) / rows[i][i]
    return answer


def checksum(rows):
    payload = [
        {
            'id': r.get('id'),
            'material': r['material'],
            'days_in_field': int(r['days_in_field']),
            'prior_checks': int(r['prior_checks']),
            'passed': bool(r['passed']),
        }
        for r in rows
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def fit(rows):
    rows = list(rows)
    failures = sum(1 for r in rows if not r['passed'])
    base = {
        'samples': len(rows),
        'failures': failures,
        'minimum_samples': MIN_SAMPLES,
        'reference_pct': REFERENCE,
        'features': list(FEATURES),
        'regularization': list(RIDGE),
        'source_checksum': checksum(rows),
    }
    if len(rows) < MIN_SAMPLES:
        return {**base, 'fitted': False, 'reason': '封口检查样本不足%d条，先复检再评估' % MIN_SAMPLES}
    if failures == 0 or failures == len(rows):
        return {**base, 'fitted': False, 'reason': '历史检查结果只有一种，无法拟合区分度，先复检'}
    x = [vector(r['material'], r['days_in_field'], r['prior_checks']) for r in rows]
    y = [0.0 if r['passed'] else 1.0 for r in rows]
    k = len(FEATURES)
    beta = [0.0] * k
    converged = False
    iterations = 0
    for iterations in range(1, 51):
        gradient = [0.0] * k
        hessian = [[0.0] * k for _ in range(k)]
        for row, target in zip(x, y):
            p = _sigmoid(sum(b * f for b, f in zip(beta, row)))
            weight = max(p * (1.0 - p), 1e-9)
            for a in range(k):
                gradient[a] += row[a] * (target - p)
                for b in range(k):
                    hessian[a][b] += weight * row[a] * row[b]
        for a in range(k):
            gradient[a] -= RIDGE[a] * beta[a]
            hessian[a][a] += RIDGE[a]
        try:
            step = _solve(hessian, gradient)
        except ValueError:
            return {**base, 'fitted': False, 'reason': '样本矩阵无法求解，先复检'}
        beta = [b + s for b, s in zip(beta, step)]
        if max(abs(s) for s in step) < 1e-9:
            converged = True
            break
    log_loss = 0.0
    for row, target in zip(x, y):
        p = min(max(_sigmoid(sum(b * f for b, f in zip(beta, row))), 1e-12), 1 - 1e-12)
        log_loss -= target * math.log(p) + (1 - target) * math.log(1 - p)
    return {
        **base,
        'fitted': True,
        'model': 'ridge-regularized Newton logistic regression (stdlib)',
        'coefficients': dict(zip(FEATURES, [round(b, 8) for b in beta])),
        'iterations': iterations,
        'converged': converged,
        'log_loss': round(log_loss / len(rows), 6),
    }


def predict(model, material, days, prior):
    if not model.get('fitted'):
        return None
    coefficients = model['coefficients']
    row = vector(material, days, prior)
    score = sum(coefficients[name] * row[i] for i, name in enumerate(FEATURES))
    return round(_sigmoid(score), 6)
