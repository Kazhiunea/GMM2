# -*- coding: utf-8 -*-
"""GMM2 (1).ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1e7JZTcLox8zoT5ifY18wBtZsb6FHKayk
"""

#download dataset
#!pip install oidv6
#!oidv6 downloader --dataset /content/drive/MyDrive/GMM2 en --type_data all --classes apple motorcycle snowman --limit 600 --yes --multi_classes --hide_metadata
#!git clone https://github.com/Paperspace/DataAugmentationForObjectDetection.git

from google.colab import drive
drive.mount('/content/drive')

"""Imports"""

import os
import sys
import numpy as np
import pandas as pd
from tqdm.notebook import tqdm
from IPython.display import display

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from PIL import Image

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

import torchvision
from torchvision import models, transforms
from torchvision.io import read_image
from torchvision.utils import draw_bounding_boxes

DATASET_LOC = "/content/drive/MyDrive/GMM2/"

"""Utils

"""

def show_image_bbox(image, *bboxes):
  fig, ax = plt.subplots()
  ax.imshow(image.permute(1, 2, 0))
  for bbox, color in zip(bboxes, ["red", "green", "blue"] * len(bboxes)):
    xc, yc, w, h = bbox
    w *= image.shape[2]
    h *= image.shape[1]
    x = image.shape[2] * xc - w/2
    y = image.shape[1] * yc - h/2
    ax.add_patch(
        Rectangle((x, y), w, h, color=color, fill=False, lw=3)
    )
  display(fig)
  plt.close()

"""Dataset


"""

class OpenImagesDataset(Dataset):
  def __init__(self, dir, subset, transforms = None):
    self.dir = dir
    self.subset = subset
    self.transforms = transforms

    self.all_samples = sorted([
      item.split('.')[0] 
      for item in os.listdir(dir + f"/multidata/{subset}/") 
      if item.endswith(".jpg")
    ])

  def __len__(self):
    return len(self.all_samples)

  def __getitem__(self, idx):
    image_path = f"{self.dir}/multidata/{self.subset}/{self.all_samples[idx]}.jpg"
    label_path = f"{self.dir}/multidata/{self.subset}/labels/{self.all_samples[idx]}.txt"

    raw_image = Image.open(image_path).convert('RGB')

    all_boxes = []
    with open(label_path, "r") as file:
      for line in file.readlines():
        raw_label, x0, y0, x1, y1 = line.split(" ")
        all_boxes.append((raw_label, float(x0), float(y0), float(x1), float(y1)))

    biggest_bbox = list(max(all_boxes, key = lambda x: (x[3] - x[1]) * (x[4] - x[2]))[1:])

    #apply transforms
    if self.transforms != None:
      image, bbox, label = self.transforms(raw_image, biggest_bbox, raw_label)
    else:
      image, bbox, label = raw_image, biggest_bbox, raw_label

    return image, label, bbox

"""#Model"""

class MyModel(nn.Module):
  def __init__(self):
    super(MyModel, self).__init__()
    base_model = models.resnet50(pretrained = True)
    num_features = 2048

    #Take list without last element
    self.root = nn.Sequential(*list(base_model.children())[:-1])

    #clasification branch
    self.class_branch = nn.Sequential(
        
        #Make X ammount of last layers neurons to 0
        nn.Dropout(0.4),
        nn.Linear(num_features, 3),
    )

    #localisation branch
    self.bbox_branch = nn.Sequential(
        nn.Linear(num_features, num_features * 2),
        nn.ReLU(),
        nn.BatchNorm1d(num_features * 2),
        nn.Linear(num_features * 2, num_features // 2),
        nn.ReLU(),
        nn.BatchNorm1d(num_features // 2),
        nn.Linear(num_features // 2, 4),
        nn.Sigmoid()
    )

  def forward(self, input):
    features = self.root(input)
    features = nn.Flatten()(features)

    classes = self.class_branch(features)
    bbox = self.bbox_branch(features)
    return classes, bbox

"""Transforms

"""

#bbox helpers
def bbox_wh_to_xy(bbox):
  w,h,xc,yc = bbox
  
  x0 = xc - w/2
  y0 = yc - h/2
  x1 = xc + w/2
  x2 = yc + h/2

  return [bbox[0], bbox[1], bbox[0] + bbox[2], bbox[1] + bbox[3]]

#x0 - 0  y0 - 1. x1 - 2  y1 - 3 
def bbox_xy_to_wh(bbox):
  w = bbox[2] - bbox[0]
  h = bbox[3] - bbox[1]
  xc = bbox[0] + w/2
  yc = bbox[1] + h/2
  return [xc, yc, w, h]

#return [bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]]

def normalize_xy_bbox(bbox, image):
  im_width, im_height = image.size
  return [bbox[0] / im_width, bbox[1] / im_height, bbox[2] / im_width, bbox[3] / im_height]

def unnormalize_xy_bbox(bbox, im_width, im_height):
  return [bbox[0] * im_width, bbox[1] * im_height, bbox[2] * im_width, bbox[3] * im_height]

#label helpers
label_ids = {
    "apple" : 0,
    "motorcycle" : 1,
    "snowman" : 2
}


#apple -> 0
def label_to_id(label):
  if label in label_ids: 
    return label_ids[label]
  else: 
    raise ValueError(f"Unknow label {label}")

#0 -> apple
def id_to_label(id):
  for label, label_id in label_ids.items():
    if id == label_id:
      return label
  raise ValueError(f"Unknow id {id}")

#main transforms
def preprocess(image, bbox, label):
  image_transforms = transforms.Compose([
    transforms.Resize((256, 256)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
  ])

  bbox = normalize_xy_bbox(bbox, image)
  bbox = bbox_xy_to_wh(bbox)
  bbox = torch.tensor(bbox)

  image = image_transforms(image)

  label = label_to_id(label)

  return image, bbox, label

"""Training and validation"""

class StatTracker():
  def __init__(self):
    #Tracking stuff
    self.total_samples = 0
    self.total_loss_class = 0
    self.total_loss_local = 0
    self.total_correct_classes = 0
    self.total_miou = 0

  def mark_step(self, num_samples, loss_class, loss_local, truth_labels, pred_labels, truth_bbox, pred_bbox):
    with torch.no_grad():
      self.total_samples += num_samples
      self.total_loss_class += loss_class * num_samples
      self.total_loss_local += loss_local * num_samples
      self.total_correct_classes += StatTracker.count_correct_classifications(pred_labels, truth_labels)
      self.total_miou += StatTracker.calc_miou(truth_bbox, pred_bbox) * num_samples

  def print_info(self, info_name = None):
      if info_name != None:
        print("==========", info_name, "==========")  

      n = self.total_samples
      print(f"Classification loss: {self.total_loss_class / n:.5f}, Localisation loss: {self.total_loss_local / n:.5f}")
      print(f"Classification acc:  {self.total_correct_classes / n:.5f}, Localisation miou:  {self.total_miou / n:.5f}")
      print(f"Total samples: {n}")


  @staticmethod
  def calc_miou(A, B):
    a = StatTracker.wh_to_xy(A)
    b = StatTracker.wh_to_xy(B)
    return float(torchvision.ops.box_iou(a, b).diagonal().mean())
  
  @staticmethod
  def wh_to_xy(BBOX):
    bbox = BBOX.clone()
    orig = BBOX.clone()
    xc, yc, w, h = 0, 1, 2, 3
    bbox[:,0] = orig[:,xc] - orig[:,w] / 2
    bbox[:,1] = orig[:,yc] - orig[:,h] / 2
    bbox[:,2] = orig[:,xc] + orig[:,w] / 2
    bbox[:,3] = orig[:,yc] + orig[:,h] / 2
    return bbox

  @staticmethod
  def count_correct_classifications(outputs, labels):
    output_labels = torch.argmax(outputs, 1)
    return int(torch.sum((labels == output_labels).float()))

def train(model, epochs, lr, train_loader, device, root_coef = 10):
  class_loss_f = nn.CrossEntropyLoss()
  local_loss_f = nn.MSELoss()

  params = [
            {"params": model.class_branch.parameters()},
            {"params": model.bbox_branch.parameters()},
            {"params": model.root.parameters(), "lr": lr / root_coef}
  ]
  optimizer = torch.optim.Adam(params, lr=lr)

  for epoch in tqdm(range(epochs), "Training"):
    stat_tracker = StatTracker()
    model.train()

    for data in tqdm(train_loader, f"epoch {epoch}"):
      images, labels, bboxes = (d.to(device) for d in data)

      optimizer.zero_grad()
      pred_class, pred_bbox = model(images)
      class_loss = class_loss_f(pred_class, F.one_hot(labels, 3).float())
      local_loss = local_loss_f(pred_bbox, bboxes) * 5
      (local_loss + class_loss).backward()
      optimizer.step()

      stat_tracker.mark_step(len(images), class_loss, local_loss, labels, pred_class, bboxes, pred_bbox)
    
    stat_tracker.print_info(f"Epoch {epoch} training set")
    get_stats(model, test_loader, device, f"Epoch {epoch} test set")


def get_stats(model, data_loader, device, name = None):
  with torch.no_grad():
    model.eval()
    class_loss_f = nn.CrossEntropyLoss()
    local_loss_f = nn.MSELoss()

    stat_tracker = StatTracker()
    for data in data_loader:
      images, labels, bboxes = (d.to(device) for d in data)
      pred_class, pred_bbox = model(images)

      class_loss = class_loss_f(pred_class, F.one_hot(labels, 3).float())
      local_loss = local_loss_f(pred_bbox, bboxes) * 5
      stat_tracker.mark_step(len(images), class_loss, local_loss, labels, pred_class, bboxes, pred_bbox)

    stat_tracker.print_info(name)

"""Main"""

#load datasets
train_ds = OpenImagesDataset(DATASET_LOC, "train", preprocess)
test_ds = OpenImagesDataset(DATASET_LOC, "test", preprocess)
validation_ds = OpenImagesDataset(DATASET_LOC, "validation", preprocess)

print(set(train_ds.all_samples) & set(test_ds.all_samples), set(train_ds.all_samples) & set(validation_ds.all_samples))

#dataloaders
BATCH_SIZE = 64
NUM_WORKERS = 2

train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, num_workers=NUM_WORKERS, shuffle=True)
test_loader = DataLoader(test_ds, batch_size=BATCH_SIZE, num_workers=NUM_WORKERS)
validation_loader = DataLoader(validation_ds, batch_size=BATCH_SIZE, num_workers=NUM_WORKERS)

#device
DEVICE = "cuda:0" if torch.cuda.is_available() else "cpu"
print("device = ", DEVICE)

#model
#model = MyModel()
model = torch.load("drive/MyDrive/GMM2/gmm2_resnet50.pth")
model.to(DEVICE);

#train
train(model, 5, 1e-4, train_loader, DEVICE, root_coef=10)

assert(False)

torch.save(model, "drive/MyDrive/GMM2/gmm2_resnet50.pth")

get_stats(model, test_loader, DEVICE, "test")
get_stats(model, validation_loader, DEVICE, "validation")

def from_image_url(image_url):
  import urllib.request
  urllib.request.urlretrieve(image_url, "tmp.png")
  img = Image.open("tmp.png")
  img_prep, _, _ = preprocess(img, [0,0,0,0], "motorcycle")
  label_pred, bbox_pred = model(img_prep[None].to(DEVICE))
  label_pred = torch.softmax(label_pred, 1)
  print(" | ".join([f"{id_to_label(i)}: {float(prob):.5f}" for i, prob in enumerate(label_pred[0])]))
  show_image_bbox(transforms.ToTensor()(img), bbox_pred[0].cpu())
  
with torch.no_grad():
  from_image_url("https://electrek.co/wp-content/uploads/sites/3/2022/01/damon-hyperfighter-header.jpg?quality=82&strip=all&w=1600")
  from_image_url("https://image.shutterstock.com/image-photo/snoman-winter-forest-260nw-1119358115.jpg")
  from_image_url("https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Apple-logo.png/640px-Apple-logo.png")
  from_image_url("https://thumbs.dreamstime.com/z/snowman-chopper-1-1491198.jpg")
  from_image_url("https://www.ocregister.com/wp-content/uploads/migration/kv6/kv6pav-19.santaride.1225.js.jpg?w=620")
  from_image_url("https://staticg.sportskeeda.com/editor/2021/11/ff780-16358380069248-1920.jpg")

from io import BytesIO
from google.colab import files

uploaded = files.upload()
im = Image.open(BytesIO(uploaded['Red-Apple.png']))

with torch.no_grad():
  img_prep, _, _ = preprocess(im, [0,0,0,0], "apple")
  label_pred, bbox_pred = model(img_prep[None].to(DEVICE))
  label_pred = torch.softmax(label_pred, 1)
  print(" | ".join([f"{id_to_label(i)}: {float(prob):.5f}" for i, prob in enumerate(label_pred[0])]))
  show_image_bbox(transforms.ToTensor()(im), bbox_pred[0].cpu())

#visualise validation set
with torch.no_grad():
  model.eval()

  raw_ds = OpenImagesDataset(DATASET_LOC, "validation")
  for i in range(len(raw_ds)):
    img, label, bbox = raw_ds[i]

    img_prep, bbox_prep, _ = preprocess(img, bbox, label)

    label_pred, bbox_pred = model(img_prep[None].to(DEVICE))
    label_pred = id_to_label(int(torch.argmax(label_pred, 1)))

    print(f"Pred: {label_pred} | Truth: {label}")
    show_image_bbox(transforms.ToTensor()(img), bbox_pred[0].cpu(), bbox_prep)