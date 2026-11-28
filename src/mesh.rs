//! Wavefront .obj -> Roblox mesh ("version 2.00"), so a Local file rule can point an asset ID
//! straight at an .obj file. Roblox can't read .obj itself; this converts it when served.
//!
//! Roblox mesh 2.00 layout (little endian):
//!   "version 2.00\n"
//!   header: u16 header_size (12), u8 vertex_size (36), u8 face_size (12), u32 vertex_count, u32 face_count
//!   vertices: position f32x3, normal f32x3, uv f32x3 (u, 1 - v, 0)
//!   faces: u32 x3 (0-based vertex indices)

use std::collections::HashMap;

const MAX_VERTICES: usize = 2_000_000;

fn index(token: &str, count: usize) -> Option<usize> {
    let i: i64 = token.parse().ok()?;
    let n = count as i64;
    let v = if i > 0 { i - 1 } else if i < 0 { n + i } else { return None };
    (0..n).contains(&v).then_some(v as usize)
}

pub fn is_obj(path: &str) -> bool {
    path.to_ascii_lowercase().ends_with(".obj")
}

/// Convert .obj text to a Roblox mesh. Polygons are split into triangles; missing normals are
/// computed (smooth); missing texture coordinates are zero.
pub fn obj_to_roblox_mesh(text: &str) -> Result<Vec<u8>, String> {
    let mut pos: Vec<[f32; 3]> = Vec::new();
    let mut uv: Vec<[f32; 2]> = Vec::new();
    let mut nrm: Vec<[f32; 3]> = Vec::new();
    // unique (position, uv, normal) corners -> output vertex
    let mut corners: HashMap<(usize, Option<usize>, Option<usize>), u32> = HashMap::new();
    let mut verts: Vec<(usize, Option<usize>, Option<usize>)> = Vec::new();
    let mut faces: Vec<[u32; 3]> = Vec::new();
    let num = |s: Option<&str>| -> Result<f32, String> {
        let v: f32 = s.ok_or("a number is missing")?.parse().map_err(|_| "a number can't be read".to_string())?;
        if v.is_finite() { Ok(v) } else { Err("a number is not finite".into()) }
    };
    for (line_no, raw) in text.lines().enumerate() {
        let line = raw.split('#').next().unwrap_or("").trim();
        let mut it = line.split_whitespace();
        let at = |e: String| format!("line {}: {e}", line_no + 1);
        match it.next() {
            Some("v") => pos.push([num(it.next()).map_err(at)?, num(it.next()).map_err(at)?, num(it.next()).map_err(at)?]),
            Some("vt") => uv.push([num(it.next()).map_err(at)?, it.next().map(|s| s.parse().unwrap_or(0.0)).unwrap_or(0.0)]),
            Some("vn") => nrm.push([num(it.next()).map_err(at)?, num(it.next()).map_err(at)?, num(it.next()).map_err(at)?]),
            Some("f") => {
                let mut poly = Vec::new();
                for corner in it {
                    let mut parts = corner.split('/');
                    let p = parts.next().and_then(|t| index(t, pos.len())).ok_or_else(|| at("bad vertex index".into()))?;
                    let t = parts.next().filter(|s| !s.is_empty()).map(|s| index(s, uv.len()).ok_or_else(|| at("bad uv index".into()))).transpose()?;
                    let n = parts.next().filter(|s| !s.is_empty()).map(|s| index(s, nrm.len()).ok_or_else(|| at("bad normal index".into()))).transpose()?;
                    let key = (p, t, n);
                    let id = match corners.get(&key) {
                        Some(&id) => id,
                        None => {
                            if verts.len() >= MAX_VERTICES {
                                return Err("the mesh has too many vertices".into());
                            }
                            let id = verts.len() as u32;
                            corners.insert(key, id);
                            verts.push(key);
                            id
                        }
                    };
                    poly.push(id);
                }
                if poly.len() < 3 {
                    return Err(at("a face needs at least 3 corners".into()));
                }
                for i in 1..poly.len() - 1 {
                    faces.push([poly[0], poly[i], poly[i + 1]]);
                }
            }
            _ => {}
        }
    }
    if faces.is_empty() {
        return Err("the file has no faces".into());
    }
    // smooth normals for corners without one
    let mut acc = vec![[0f32; 3]; pos.len()];
    for f in &faces {
        let [a, b, c] = f.map(|i| pos[verts[i as usize].0]);
        let (u, v) = ([b[0] - a[0], b[1] - a[1], b[2] - a[2]], [c[0] - a[0], c[1] - a[1], c[2] - a[2]]);
        let n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
        for &i in f {
            let p = verts[i as usize].0;
            for k in 0..3 {
                acc[p][k] += n[k];
            }
        }
    }
    let mut out = Vec::with_capacity(25 + verts.len() * 36 + faces.len() * 12);
    out.extend_from_slice(b"version 2.00\n");
    out.extend_from_slice(&12u16.to_le_bytes());
    out.push(36);
    out.push(12);
    out.extend_from_slice(&(verts.len() as u32).to_le_bytes());
    out.extend_from_slice(&(faces.len() as u32).to_le_bytes());
    for &(p, t, n) in &verts {
        let normal = match n {
            Some(n) => nrm[n],
            None => {
                let a = acc[p];
                let len = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt();
                if len > 0.0 { [a[0] / len, a[1] / len, a[2] / len] } else { [0.0, 1.0, 0.0] }
            }
        };
        let [tu, tv] = t.map(|t| uv[t]).unwrap_or([0.0, 0.0]);
        for f in pos[p].iter().chain(normal.iter()).chain([tu, 1.0 - tv, 0.0].iter()) {
            out.extend_from_slice(&f.to_le_bytes());
        }
    }
    for f in &faces {
        for i in f {
            out.extend_from_slice(&i.to_le_bytes());
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u32_at(b: &[u8], i: usize) -> u32 {
        u32::from_le_bytes(b[i..i + 4].try_into().unwrap())
    }
    fn f32_at(b: &[u8], i: usize) -> f32 {
        f32::from_le_bytes(b[i..i + 4].try_into().unwrap())
    }

    #[test]
    fn quad_becomes_two_triangles() {
        let obj = "# a square\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3 4/4\n";
        let m = obj_to_roblox_mesh(obj).unwrap();
        assert!(m.starts_with(b"version 2.00\n"));
        let h = 13;
        assert_eq!(u16::from_le_bytes([m[h], m[h + 1]]), 12);
        assert_eq!((m[h + 2], m[h + 3]), (36, 12));
        assert_eq!(u32_at(&m, h + 4), 4);
        assert_eq!(u32_at(&m, h + 8), 2);
        assert_eq!(m.len(), 13 + 12 + 4 * 36 + 2 * 12);
        // first vertex: position 0,0,0, normal +z (computed), uv (0, 1 - 0)
        let v0 = h + 12;
        assert_eq!(f32_at(&m, v0 + 20), 1.0);
        assert_eq!(f32_at(&m, v0 + 28), 1.0);
        let faces = h + 12 + 4 * 36;
        assert_eq!([u32_at(&m, faces), u32_at(&m, faces + 4), u32_at(&m, faces + 8)], [0, 1, 2]);
        assert_eq!([u32_at(&m, faces + 12), u32_at(&m, faces + 16), u32_at(&m, faces + 20)], [0, 2, 3]);
    }

    #[test]
    fn normals_and_negative_indices() {
        let obj = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 -1\nf -3//1 -2//1 -1//1\n";
        let m = obj_to_roblox_mesh(obj).unwrap();
        assert_eq!(u32_at(&m, 17), 3);
        assert_eq!(f32_at(&m, 25 + 20), -1.0, "the file's own normal is kept");
    }

    #[test]
    fn rejects_broken_files() {
        assert!(obj_to_roblox_mesh("").is_err());
        assert!(obj_to_roblox_mesh("v 0 0 0\nf 1 2 3\n").is_err());
        assert!(obj_to_roblox_mesh("v 0 0 x\n").is_err());
        assert!(obj_to_roblox_mesh("v 0 0 0\nv 1 0 0\nf 1 2\n").is_err());
    }
}
