"""Check which Reisecker 2023 supplementary movies show the same sample as SI figure photos.

SIFT + RANSAC similarity registration of a movie frame against each SI figure raster (the SI docx
images). Many inliers with a consistent scale/rotation => same sample and view. Used to tie movies to
samples whose CCD is stated (Supp. Fig. 9: SC DC, highest CCD; Supp. Fig. 10: HP PP, CCD 6.6 mA/cm2;
Supp. Fig. 11c: HP sample shown in Movie 5; Supp. Fig. 12: reversible SC sample = Movie 4).

Usage:
  uv run --with opencv-python-headless --with numpy python -I match_movies_to_si.py <si_media_dir> <movie_dir>
"""
import sys
from pathlib import Path

import cv2
import numpy as np

SI_IMAGES = {"SuppFig9": "image9.png", "SuppFig10": "image10.png", "SuppFig11": "image11.png",
             "SuppFig12": "image12.png"}
MOVIE_CROP = {4: (0, 0, 905, 1080)}


def gray(img):
    if img.ndim == 3 and img.shape[2] == 4:  # composite RGBA on white
        a = img[:, :, 3:4] / 255.0
        img = (img[:, :, :3] * a + 255 * (1 - a)).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def frame(path, frac):
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * frac) - 1))
    ok, fr = cap.read()
    cap.release()
    return fr


def main(si_dir, movie_dir):
    sift = cv2.SIFT_create()
    clahe = cv2.createCLAHE(2.0, (8, 8))
    si = {}
    for k, f in SI_IMAGES.items():
        img = gray(cv2.imread(str(Path(si_dir) / f), cv2.IMREAD_UNCHANGED))
        img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        si[k] = sift.detectAndCompute(clahe.apply(img), None)
    bf = cv2.BFMatcher()
    print("movie,frame_frac,si_figure,good,inliers,rotation_deg,scale")
    for m in range(1, 6):
        p = Path(movie_dir) / f"movie{m}.mp4"
        for frac in (0.05, 1.0):
            fr = frame(p, frac)
            if m in MOVIE_CROP:
                a, b, c, d = MOVIE_CROP[m]
                fr = fr[b:d, a:c]
            g = cv2.resize(gray(fr), None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
            kp, des = sift.detectAndCompute(clahe.apply(g), None)
            for k, (kp2, des2) in si.items():
                good = [a for a, b in (x for x in bf.knnMatch(des, des2, k=2) if len(x) == 2)
                        if a.distance < 0.75 * b.distance]
                if len(good) < 6:
                    print(f"{m},{frac},{k},{len(good)},0,,")
                    continue
                M, inl = cv2.estimateAffinePartial2D(np.float32([kp[x.queryIdx].pt for x in good]),
                                                     np.float32([kp2[x.trainIdx].pt for x in good]),
                                                     method=cv2.RANSAC, ransacReprojThreshold=3.0)
                if M is None:
                    print(f"{m},{frac},{k},{len(good)},0,,")
                    continue
                rot = np.degrees(np.arctan2(M[1, 0], M[0, 0]))
                print(f"{m},{frac},{k},{len(good)},{int(inl.sum())},{rot:.1f},{np.hypot(M[0,0],M[1,0]):.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
