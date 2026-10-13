"""Seguimiento por IoU, sin dependencias: da ids estables a las detecciones entre cuadros."""

def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    i = max(0, x2 - x1) * max(0, y2 - y1)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0

class Seguidor:
    """Empareja cajas con pistas por IoU (voraz, de mayor a menor) y misma clase. Una pista sobrevive `paciencia` s sin verse."""
    def __init__(self, iou_min=0.3, paciencia=2.0):
        self.iou_min, self.paciencia, self.pistas, self.sig = iou_min, paciencia, {}, 1

    def actualizar(self, dets, ahora):
        """dets: [(clase, confianza, caja)] -> [(id, clase, confianza, caja)]"""
        pares = sorted(((iou(p["caja"], d[2]), i, j) for i, p in self.pistas.items() for j, d in enumerate(dets) if p["clase"] == d[0]), reverse=True)
        usadas_p, usadas_d, res = set(), set(), []
        for v, i, j in pares:
            if v < self.iou_min: break
            if i in usadas_p or j in usadas_d: continue
            usadas_p.add(i); usadas_d.add(j)
            self.pistas[i].update(caja=dets[j][2], visto=ahora); res.append((i, dets[j][0], dets[j][1], dets[j][2]))
        for j, d in enumerate(dets):
            if j in usadas_d: continue
            self.pistas[self.sig] = {"clase": d[0], "caja": d[2], "visto": ahora}; res.append((self.sig, d[0], d[1], d[2])); self.sig += 1
        for i in [i for i, p in self.pistas.items() if ahora - p["visto"] > self.paciencia]: del self.pistas[i]
        return res
