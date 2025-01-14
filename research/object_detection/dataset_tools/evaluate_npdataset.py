import numpy as np
import tensorflow as tf

from io import StringIO
import argparse
import cv2
import json
from shutil import copytree
import os
import fnmatch
from os.path import abspath, join, splitext, basename

parser = argparse.ArgumentParser()

parser.add_argument("--np_dataset", type=str, help="")
parser.add_argument("--pb_file", type=str, help="pb_file")
parser.add_argument("--out_folder", help=None, type=str)
args = parser.parse_args()

def find_image_files(np_folder):
    photos_path = join(np_folder, 'photos')
    if not os.path.exists(photos_path):
        photos_path = join(np_folder, 'Photos')
    assert os.path.exists(photos_path), "photo folder is not found at {}".format(photos_path)
    all_files = os.listdir(photos_path)
    photo_files = []
    for file in all_files:
        if not file.startswith("."):
            if    fnmatch.fnmatch(file, '*.jpg') \
               or fnmatch.fnmatch(file, '*.JPG'):
                photo_files.append(os.path.join(photos_path, file))
    photo_files = sorted(photo_files)
    return photo_files, photos_path, os.path.join(np_folder, "templates.json")



def run_inference_for_single_image(image, graph):
    with graph.as_default():
        with tf.Session() as sess:
            # Get handles to input and output tensors
            ops = tf.get_default_graph().get_operations()
            all_tensor_names = {output.name for op in ops for output in op.outputs}
            tensor_dict = {}
            for key in [
                'num_detections', 'detection_boxes', 'detection_scores',
                'detection_classes', 'detection_masks'
            ]:
                tensor_name = key + ':0'
                if tensor_name in all_tensor_names:
                    tensor_dict[key] = tf.get_default_graph().get_tensor_by_name(
                        tensor_name)

            image_tensor = tf.get_default_graph().get_tensor_by_name('image_tensor:0')

            # Run inference
            output_dict = sess.run(tensor_dict,
                                   feed_dict={image_tensor: np.expand_dims(image, 0)})

            # all outputs are float32 numpy arrays, so convert types as appropriate
            output_dict['num_detections'] = int(output_dict['num_detections'][0])
            output_dict['detection_classes'] = output_dict[
                'detection_classes'][0].astype(np.uint8)
            output_dict['detection_boxes'] = output_dict['detection_boxes'][0]
            output_dict['detection_scores'] = output_dict['detection_scores'][0]
            return output_dict

def batch_iou(boxes, box):
  """Compute the Intersection-Over-Union of a batch of boxes with another
  box.

  Args:
    box1: 2D array of [l, t, r, b].
    box2: a single array of [l, t, r, b]
  Returns:
    ious: array of a float number in range [0, 1].
  """
  lr = np.maximum(
      np.minimum(boxes[:,2], box[2]) - \
      np.maximum(boxes[:,0], box[0]),
      0
  )
  tb = np.maximum(
      np.minimum(boxes[:,3], box[3]) - \
      np.maximum(boxes[:,1], box[1]),
      0
  )
  inter = lr*tb
  union = (boxes[:,2]-boxes[:,0])*(boxes[:,3]-boxes[:,1]) + \
          (box[2]-box[0])*(box[3]-box[1]) - inter
  return inter/union

def nms(boxes, probs, threshold):
  """Non-Maximum supression.
  Args:
    boxes: array of [x, y, x2, y2] (l,b,r,t)format
    probs: array of probabilities
    threshold: two boxes are considered overlapping if their IOU is largher than
        this threshold
    form: 'center' or 'diagonal'
  Returns:
    keep: array of True or False.
  """
  boxes=np.array(boxes)
  probs=np.array(probs)
  order = probs.argsort()[::-1]
  keep = [True]*len(order)

  for i in range(len(order)-1):
    ovps = batch_iou(boxes[order[i+1:]], boxes[order[i]])
    for j, ov in enumerate(ovps):
      if ov > threshold:
        keep[order[j+i+1]] = False
  return keep

if __name__ == '__main__':
    input_folder = abspath(args.np_dataset)
    output_folder= abspath(args.out_folder)
    detection_graph = tf.Graph()
    with detection_graph.as_default():
        od_graph_def = tf.GraphDef()
        with tf.gfile.GFile(args.pb_file, 'rb') as fid:
            serialized_graph = fid.read()
            od_graph_def.ParseFromString(serialized_graph)
            tf.import_graph_def(od_graph_def, name='')

    photos, photos_path, template=find_image_files(input_folder)

    if not os.path.exists(output_folder):
        os.mkdir(output_folder)

    class_id=['back_ground']
    with open(template,"rb") as fp:
        labels= json.load(fp);
        label_def= labels.get('categories', None)[0].get("skus")
        dataset_name=labels.get("datasets")[0].get("name");
        for label in label_def:
            class_id.append(label.get("id"))

    for photo in photos:
        img=cv2.imread(photo)
        img=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        #image_np_expanded = np.expand_dims(img, axis=0)
        basename=os.path.basename(photo)

        tf.logging.warn("detecting file %s"%(basename))
        output_dict = run_inference_for_single_image(img, detection_graph)
        boxes  = output_dict['detection_boxes'].tolist()
        classes= output_dict['detection_classes'].tolist()
        scores = output_dict['detection_scores'].tolist()
        idx = 0
        for idx , score in enumerate(scores):
            if score < 0.3:
                break
        boxes=boxes[0:idx+1]
        classes=classes[0:idx+1]
        scores=scores[0:idx+1]

        keeps=nms(boxes,scores,0.45)

        w = img.shape[1]
        h = img.shape[0]
        with open(os.path.join(output_folder,basename+".json"),"wb") as output_fp:
            output_fp.write("""{
    "version": "1.0.0",
    "company": "RB_Test",
    "dataset": "photos",
    "filename": "%s",
    "image_width": %d,
    "image_height": %d,
    "bndboxes": [
"""%(basename, w,h))
            str=""
            for box, c ,score, keep in zip(boxes,classes,scores,keeps):
                if score < 0.4 or not keep:
                    continue

                ymin, xmin, ymax, xmax = box
                str+="""
        {
            "x": %f,
            "y": %f,
            "w": %f,
            "h": %f,
            "id": "%s",
            "strokeStyle": "#3399FF",
            "fillStyle": "#00FF00"
        },"""%(xmin*w,ymin*h, (xmax-xmin)*w, (ymax-ymin)*h, class_id[int(c)])


            if len(str) > 0:
                str=str[:-1]

            output_fp.write(str)
            output_fp.write("\n]}")


        #break




