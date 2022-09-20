#!/bin/bash
python tracking_offline.py --input /opt/data/dongyang/code/DaGAN/project/metahuman.mp4 --res_folder /opt/data/dongyang/code/FaceVerse/example/metahuman --version 2 --use_simplification && \
python tracking_offline.py --input /opt/data/dongyang/code/DaGAN/project/dongyang.mp4 --res_folder /opt/data/dongyang/code/FaceVerse/example/dongyang --version 2 --use_simplification && \
python tracking_offline.py --input /opt/data/dongyang/code/DaGAN/project/video1.mp4 --res_folder /opt/data/dongyang/code/FaceVerse/example/video1 --version 2 --use_simplification && \
python tracking_offline.py --input /opt/data/dongyang/code/DaGAN/project/video2.mp4 --res_folder /opt/data/dongyang/code/FaceVerse/example/video2 --version 2 --use_simplification && \
python tracking_offline.py --input /opt/data/dongyang/code/DECA/TestSamples/meta_human_woman.mp4 --res_folder /opt/data/dongyang/code/FaceVerse/example/meta_human_woman --version 2 --use_simplification
