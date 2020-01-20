import os
from typing import Tuple
import csv
from io import StringIO

# fastapi
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

# sql
import pymysql
from pymysql.cursors import DictCursor

# Image
import base64
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# S3
import s3


# Environment variables
MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_PORT = int(os.getenv('MYSQL_PORT'))
MYSQL_DB = os.getenv('MYSQL_DB')

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=['*'])

connection = None


@app.on_event('startup')
def startup():
    global connection
    connection = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, passwd=MYSQL_PASSWORD, db=MYSQL_DB)


@app.on_event('shutdown')
def shutdown():
    connection.close()


def image_to_data_uri(img: Image.Image):
    buffered = BytesIO()
    img.save(buffered, 'JPEG')
    img_base64 = base64.b64encode(buffered.getvalue())
    data_uri_byte = bytes("data:image/jpeg;base64,",
                          encoding='utf-8') + img_base64
    data_uri_string = data_uri_byte.decode('utf-8')
    return data_uri_string


def draw_box(img, lt_corner: Tuple[int], rb_corner: Tuple[int], title: str):
    draw = ImageDraw.Draw(img)
    draw.rectangle([lt_corner, rb_corner], outline="red", width=2)
    draw.text(lt_corner, title, font=ImageFont.truetype(
        "font/RobotoMono-Bold.ttf", size=16))
    return img


def get_s3_image(uri: str):
    img_stream = s3.get_file_stream(uri)
    return Image.open(img_stream)


def get_latest_result():
    connection.ping(reconnect=True)
    with connection.cursor(cursor=DictCursor) as cursor:
        # Get latest face_image_id
        query_latest_face_image = ("SELECT id, image_path, camera_id, branch_id, `time`, "
                                   "       position_top, position_right, position_bottom, position_left "
                                   "FROM FaceImage "
                                   "ORDER BY epoch DESC "
                                   "LIMIT 1;")
        cursor.execute(query_latest)
        face_image_row = cursor.fetchone()
        face_image_id = row['id']

        # Get Gender Result
        query_gender = ("SELECT type, confidence "
                        "FROM Gender "
                        "WHERE face_image_id=%s;")
        cursor.execute(query_all_result, (face_image_id,))
        gender_row = cursor.fetchone()

        # Get Race Result
        query_race = ("SELECT type, confidence "
                      "FROM Race "
                      "WHERE face_image_id=%s;")
        cursor.execute(query_all_result, (face_image_id,))
        race_row = cursor.fetchone()

    return face_image_row, gender_row, race_row


@app.get("/_api/result/latest")
def result_latest():
    # Get all rows
    face_image_row, gender_row, race_row = get_latest_result()

    # Get image
    image = get_s3_image(face_image_row['image_path'])

    # Draw box
    image_with_box = draw_box(image,
                              (face_image_row['position_left'], face_image_row['position_top']),
                              (face_image_row['position_right'], face_image_row['position_bottom']), "")

    # Insert one result
    results = [{
        'gender': {
            'type': gender_row['type'],
            'confidence': gender_row['confidence']
        },
        'race': {
            'type': race_row['type'],
            'confidence': race_row['confidence']
        }
    }]

    return {'epoch': face_image_row['time'],
            'branch_id': face_image_row['branch_id'],
            'camera_id': face_image_row['camera_id'],
            'results': results,
            'photo_data_uri': image_to_data_uri(image_with_box)}


@app.get("/_api/result/csv")
def result_csv(start: int, end: int):

    # get data from DB
    connection.ping(reconnect=True)
    with connection.cursor(cursor=DictCursor) as cursor:
        query_latest = ("SELECT epoch, branch_id, camera_id, filepath,"
                        "       gender, gender_confident AS gender_confidence,"
                        "       race, race_confident AS race_confidence "
                        "FROM data "
                        "WHERE epoch BETWEEN %s AND %s "
                        "ORDER BY epoch DESC ")
        cursor.execute(query_latest, (int(start), int(end)))
        rows = cursor.fetchall()

    # transform to csv
    if not rows:
        return {}  # TODO: return 204 code
    csv_stream = StringIO()
    csv_writer = csv.DictWriter(csv_stream, fieldnames=list(rows[0].keys()))
    csv_writer.writeheader()
    csv_writer.writerows(rows)
    csv_stream.seek(0)

    # send to response
    csv_name = "result-start-{}-to-{}.csv".format(start, end)
    return StreamingResponse(csv_stream, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="{}"'.format(csv_name)})
