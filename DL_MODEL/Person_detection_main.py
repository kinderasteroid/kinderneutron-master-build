import asyncio
import cv2
import numpy as np
import requests
import os
import json
import time
from neo4j import GraphDatabase
from DatabaseUpdate import Database_Update as kinderneutron
import copy
import pika

RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'rabbitmq')
RABBITMQ_PORT = os.getenv('RABBITMQ_PORT', '5672')
RABBITMQ_USERNAME = os.getenv('RABBITMQ_DEFAULT_USER', 'admin')
RABBITMQ_PASSWORD = os.getenv('RABBITMQ_DEFAULT_PASS', 'admin')

# Define global variables
NEAR_DISTANCE_THRESHOLD = 900  # Example threshold for near detection (pixels)
FAR_DISTANCE_THRESHOLD = 50    # Example threshold for far detection (pixels)
filepath = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data.json'))
video_feed_url = 'http://kinderneutronapicontainer:8001/videostreamapi'

# Load YOLO
kn = kinderneutron()
net = cv2.dnn.readNet("yolov3.weights", "yolov3.cfg")
layer_names = net.getUnconnectedOutLayersNames()
global person_detection_status
global temp_detection_status
# Initialize dictionary for person detection status
person_detection_status = {'near': False, 'far': False}
temp_detection_status={'near':False,'far':False}
# Asynchronous function to process video frames and perform object detection
async def process_frame(frame):
    height, width, _ = frame.shape
    blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (218, 218), swapRB=True, crop=False)
    net.setInput(blob)
    detections = net.forward(layer_names)

    detected_near = False
    detected_far = False

    for detection in detections:
        for obj in detection:
            scores = obj[5:]
            class_id = np.argmax(scores)
            confidence = scores[class_id]

            if confidence > 0.5 and class_id == 0:  # Class ID 0 represents a person in COCO dataset
                center_x = int(obj[0] * width)
                center_y = int(obj[1] * height)
                w = int(obj[2] * width)
                h = int(obj[3] * height)

                box_size = max(w, h)
                if 450 < box_size <= NEAR_DISTANCE_THRESHOLD:
                    detected_near = True
                elif 450 >= box_size >= FAR_DISTANCE_THRESHOLD:
                    detected_far = True

                x = int(center_x - w/2)
                y = int(center_y - h/2)

                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

    return frame, detected_near, detected_far

# Asynchronous function to fetch and process video frames
async def process_video_feed_async(url):
    global person_detection_status
    global temp_detection_status
    credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)
    connection_params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT, credentials=credentials)
    connection = pika.BlockingConnection(connection_params)
    channel = connection.channel()
    channel.queue_declare(queue='person_detection', durable=False) 


    response = requests.get(url, stream=True)
    if response.status_code != 200:
        print("Error fetching video feed:", response.status_code)
        return

    bytes_data = bytes()
    for chunk in response.iter_content(chunk_size=10):
        bytes_data += chunk
        a = bytes_data.find(b'\xff\xd8')  # Start of frame
        b = bytes_data.find(b'\xff\xd9')  # End of frame
        if a != -1 and b != -1:
            frame_data = bytes_data[a:b + 2]
            bytes_data = bytes_data[b + 2:]
            frame = cv2.imdecode(np.frombuffer(frame_data, dtype=np.uint8), cv2.IMREAD_COLOR)
            processed_frame, detected_near, detected_far = await process_frame(frame)
            
            if detected_near:
                # print("Person Detected Near Camera")
                temp_detection_status['near']=True
                
            else:
                temp_detection_status['near']=False
            
            if detected_far:
                # print("Person Detected Far from Camera")
                temp_detection_status['far'] = True
            else:
                temp_detection_status['far'] = False
            
            print(temp_detection_status,'temp_status')
            print(person_detection_status,'person_detection_status')
            if temp_detection_status.get('near') != person_detection_status.get('near') or temp_detection_status.get('far') != person_detection_status.get('far'):
                print('Detection status changed. Publishing message.')
                print(' ')
                print('----------------------------------------------')
                print(' ')
                person_detection_status = copy.deepcopy(temp_detection_status)
                print('New person detection status:', person_detection_status)
                channel.basic_publish(exchange='', routing_key='person_detection', body=json.dumps(person_detection_status),
                                    properties=pika.BasicProperties(delivery_mode=2))
            else:
                print('Detection status unchanged. Not publishing message.')

async def main():
    tasks = [process_video_feed_async(video_feed_url) for _ in range(4)]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    while True:
        asyncio.run(main())
