# Person detection model

MobileNet SSD in Caffe format, trained on COCO. Person is class id 15.

## Setup on Raspberry Pi

```bash
cd models
wget https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt -O MobileNetSSD_deploy.prototxt
wget https://drive.google.com/uc?id=0B3gersZ2cHIxRm5PMWRoTkdHdHc -O MobileNetSSD_deploy.caffemodel
```

## Why this model

Runs on the Pi 4 CPU at roughly 5 to 8 frames per second, weighs about 23 MB and needs no
accelerator. Accuracy is good enough to tell whether a room is occupied.

## Alternatives

YOLOv8n with a Coral USB accelerator gives about 30 frames per second but needs the external TPU.
MediaPipe Pose returns a full skeleton and is too heavy for the Pi.
