import os
from typing import Tuple
import csv
from io import StringIO
import time

# fastapi
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from pydantic import BaseModel

# sql
import pymysql
from pymysql.cursors import DictCursor

# Image
import base64
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

# S3
import s3

# logging
import logging
logger = logging.getLogger("api")

# Environment variables
MYSQL_HOST = os.getenv('MYSQL_HOST')
MYSQL_USER = os.getenv('MYSQL_USER')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD')
MYSQL_PORT = int(os.getenv('MYSQL_PORT'))
MYSQL_DB = os.getenv('MYSQL_DB')

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=['*'])


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
    connection = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, passwd=MYSQL_PASSWORD, db=MYSQL_DB, autocommit=True)
    with connection.cursor(cursor=DictCursor) as cursor:
        # Get latest face_image_id
        query_latest_face_image = ("SELECT id, image_path, camera_id, branch_id, `time`, "
                                   "       position_top, position_right, position_bottom, position_left "
                                   "FROM FaceImage "
                                   "ORDER BY time DESC "
                                   "LIMIT 1;")
        cursor.execute(query_latest_face_image)
        face_image_row = cursor.fetchone()
        face_image_id = face_image_row['id']

        # Get Gender Result
        query_gender = ("SELECT type, confidence "
                        "FROM Gender "
                        "WHERE face_image_id=%s;")
        cursor.execute(query_gender, (face_image_id,))
        gender_row = cursor.fetchone()

        # Get Race Result
        query_race = ("SELECT type, confidence "
                      "FROM Race "
                      "WHERE face_image_id=%s;")
        cursor.execute(query_race, (face_image_id,))
        race_row = cursor.fetchone()

        # Get Age Result
        query_age = ("SELECT min_age, max_age, confidence "
                      "FROM Age "
                      "WHERE face_image_id=%s;")
        cursor.execute(query_age, (face_image_id,))
        age_row = cursor.fetchone()
    connection.close()
    logger.debug(face_image_row, gender_row, race_row, age_row)
    return face_image_row, gender_row, race_row, age_row


@app.get("/_api/result/latest")
def result_latest():
    # Get all rows
    face_image_result, gender_result, race_result, age_result = get_latest_result()

    # Get image
    image = get_s3_image(face_image_result['image_path'])

    # # Draw box
    # image_with_box = draw_box(image,
    #                           (face_image_row['position_left'], face_image_row['position_top']),
    #                           (face_image_row['position_right'], face_image_row['position_bottom']), "")

    # Insert one result
    results = [{
        'gender': {
            'type': gender_result['type'],
            'confidence': gender_result['confidence']
        },
        'race': {
            'type': race_result['type'],
            'confidence': race_result['confidence']
        },
        'age': {
            'min_age': age_result['min_age'],
            'max_age': age_result['max_age'],
            'confidence': age_result['confidence']
        }
    }]

    return {'epoch': face_image_result['time'],
            'id': face_image_result['id'],
            'branch_id': face_image_result['branch_id'],
            'camera_id': face_image_result['camera_id'],
            'results': results,
            'photo_data_uri': image_to_data_uri(image)}


@app.get("/_api/result/csv")
def result_csv(start: int = None,
               end: int = None,
               race: str = None,
               gender: str = None,
               min_age: int = None,
               max_age: int = None,
               branch: int = None,
               camera: int = None,
               min_gender_confidence: float = None,
               max_gender_confidence: float = None,
               min_age_confidence: float = None,
               max_age_confidence: float = None,
               min_race_confidence: float = None,
               max_race_confidence: float = None):

    # get data from DB
    connection = pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        passwd=MYSQL_PASSWORD,
        db=MYSQL_DB,
        autocommit=True
    )
    rows = None
    with connection.cursor(cursor=DictCursor) as cursor:
        query = ("SELECT "
                 "   FaceImage.time AS time, "
                 "   FaceImage.branch_id AS branch_id, "
                 "   FaceImage.camera_id AS camera_id, "
                 "   FaceImage.image_path AS filepath, "
                 "   Gender.type AS gender, "
                 "   Gender.confidence AS gender_confidence, "
                 "   Age.max_age AS max_age, "
                 "   Age.min_age AS min_age, "
                 "   Age.confidence AS age_confidence, "
                 "   Race.type AS race, "
                 "   Race.confidence AS race_confidence "
                 "FROM "
                 "   FaceImage "
                 "INNER JOIN "
                 "   Gender ON FaceImage.id = Gender.face_image_id "
                 "INNER JOIN "
                 "   Age ON FaceImage.id = Age.face_image_id "
                 "INNER JOIN "
                 "   Race ON FaceImage.id = Race.face_image_id "
                 "WHERE ")
        is_first_query = True
        if branch is None and camera is None and max_gender_confidence is None and max_race_confidence is None and max_age_confidence is None and min_gender_confidence is None and min_age_confidence is None and min_race_confidence is None and start is None and end is None and race is None and gender is None and min_age is None and max_age is None:
            return {}
        if start is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += "FaceImage.time >= %(start)s "
        if end is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " FaceImage.time <= %(end)s "
        if race is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Race.type like %(race)s "
        if gender is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Gender.type like %(gender)s "
        if min_age is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Age.min_age >= %(min_age)s "
        if max_age is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Age.max_age <= %(max_age)s "
        if min_gender_confidence is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Gender.confidence >= %(min_gender_confidence)s "
        if min_age_confidence is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Gender.confidence <= %(min_age_confidence)s "
        if min_race_confidence is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Race.confidence >= %(min_race_confidence)s "
        if max_gender_confidence is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Race.confidence <= %(max_gender_confidence)s "
        if max_age_confidence is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Age.confidence >= %(max_age_confidence)s "
        if max_race_confidence is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " Age.confidence <= %(max_race_confidence)s "
        if branch is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " FaceImage.branch_id <= %(branch)s "
        if camera is not None:
            if is_first_query:
                is_first_query = False
            else:
                query += " AND "
            query += " FaceImage.camera_id <= %(camera)s "
        print(query)
        effected_row = cursor.execute(query, {
            "start": start,
            "end": end,
            "race": "%{}%".format(race),
            "gender": "%{}%".format(gender),
            "branch": branch,
            "camera": camera,
            "max_age": max_age,
            "min_age": min_age,
            "min_gender_confidence": min_gender_confidence,
            "min_age_confidence": min_age_confidence,
            "min_race_confidence": min_race_confidence,
            "max_gender_confidence": max_gender_confidence,
            "max_age_confidence": max_age_confidence,
            "max_race_confidence": max_race_confidence
        })
        
        rows = cursor.fetchall()
    connection.close()
    # transform to csv
    if not rows:
        # TODO: return 204 code
        return {}
    csv_stream = StringIO()
    csv_writer = csv.DictWriter(csv_stream, fieldnames=list(rows[0].keys()))
    csv_writer.writeheader()
    csv_writer.writerows(rows)
    csv_stream.seek(0)

    # send to response
    csv_name = "result-{}.csv".format(int(time.time()))
    return StreamingResponse(csv_stream, media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="{}"'.format(csv_name)})

# For check with probe in openshift


@app.get('/healthz')
def health_check():
    return
