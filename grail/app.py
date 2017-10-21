"""
Grail Publishing

A simple publishing service, built on AWS Chalice
"""
import logging
import os
from hashlib import md5
from io import BytesIO

import requests
from jinja2 import Environment, FileSystemLoader

import boto3
from chalice import Chalice, Response
from PIL import Image

TEMPLATE_PATH = os.path.join(
    os.path.abspath(os.path.dirname(__file__)), 'chalicelib', 'templates')

S3_BUCKET = os.environ['S3_BUCKET']
ELASTICSEARCH = os.environ['ELASTICSEARCH_URL']


app = Chalice(app_name='shoebox')
app.debug = True
s3 = boto3.client('s3')
rekog = boto3.client('rekognition')
template_env = Environment(loader=FileSystemLoader(TEMPLATE_PATH))


def save_to_s3(data, key, content_type='image/png'):
    """
    Save a public file to S3, at the given key and content type.

    Expects data as bytes. To save text, encode it in your
    encoding of choice before passing it to this function.
    """
    metadata = {
        'ContentType': content_type,
        'ContentLength': len(data)
    }
    try:
        stream = BytesIO(data)
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=stream,
                      ACL='public-read', **metadata)
    finally:
        stream.close()
    metadata['key'] = key
    metadata['url'] = f'https://s3.amazonaws.com/{S3_BUCKET}/{key}'
    return metadata


def make_thumbnail(image_data, size=(360, 280), img_format='PNG'):
    """
    Given an image, as bytes, create a thumbnail of the specified
    size and format.
    """
    thumb = None
    try:
        stream = BytesIO(image_data)
        image = Image.open(stream)
        image.thumbnail(size)
    finally:
        stream.close()
    try:
        outstream = BytesIO()
        image.save(outstream, format=img_format)
        outstream.seek(0)
        thumb = outstream.getvalue()
    finally:
        outstream.close()
    return thumb


def search_xkcd(*terms):
    """
    Searches an Elasticsearch index of XKCD posts. Each
    document in the index contains a transcript of a single
    comic, here we do a full text search query against
    that transcript. Query terms are OR'd together.

    Returns:
        A list of documents matching the query terms,
        ordered by relevance.
        See an example here: https://xkcd.com/info.0.json. If
        there are no results, an empty list is returned
    """
    query = {
        "query": {
            "match": {
                "transcript": {
                    "query": " ".join(terms),
                    "operator": "or"
                }
            }
        }
    }
    resp = requests.get(ELASTICSEARCH, json=query)
    if resp.status_code == 200:
        hits = resp.json()['hits']['hits']
        for hit in hits:
            yield hit['_source']
    else:
        yield


def get_image_labels(image_metadata, max_labels=5, min_confidence=50.0):
    """
    Queries the Rekognition API to get a set of labels for the
    given image (which has already been stored in S3).

    Returns:
        The response from Rekognition, which is a dict
        containing a list of 'Labels', as well as an
        'OrientationCorrection' suggestion. See:
        http://boto3.readthedocs.io/en/latest/reference/services/rekognition.html#Rekognition.Client.detect_labels
    """
    response = rekog.detect_labels(
        Image={
            'S3Object': {
                'Bucket': S3_BUCKET,
                'Name': image_metadata['key']}
        },
        MaxLabels=max_labels,
        MinConfidence=min_confidence)
    return response


def get_posts(query=None):
    """
    Returns a list of dicts describing the HTML files contained
    in the  S3 bucket, filtered by an optional prefix query.
    """
    kwargs = {'Bucket': S3_BUCKET}
    if query:
        kwargs['Prefix'] = query
    response = s3.list_objects_v2(**kwargs)
    posts = response['Contents']
    for post in posts:
        if post['Key'].endswith('.html'):
            name = post['Key'].rsplit('.')[0]
            yield {
                'last_modified': post['LastModified'],
                'url': f"https://s3.amazonaws.com/cml-images/{post['Key']}",
                'thumbnail': f'https://s3.amazonaws.com/cml-images/images/{name}_thumbnail.png',
                'name': name
            }


def make_html(context, template_name='post.html'):
    """
    Create an HTML file with the given template and context.
    """
    template = template_env.get_template(template_name)
    return template.render(**context)


@app.route('/ping')
def ping():
    return "PONG"


@app.route('/brew', methods=['BREW', 'POST'])
def brew():
    # RFC 2324 compliance
    return Response(status_code=418)


@app.route('/upload/{name}', methods=['POST'], content_types=['image/png'])
def upload(name):
    """
    Upload an image, store in S3.
    Returns a link to an HTML resource, served from S3. The HTML
    file will include image labels from the AWS Rekognition service,
    as well as relevant XKCD comics, where "relevance" means that
    labels from Rekognition are matched against the transcripts
    of the comics using a full text search.
    """
    try:
        # Save the primary image to S3
        img_data = app.current_request.raw_body
        img_hash = md5(img_data).hexdigest()
        key = f'images/{img_hash}.png'
        metadata = save_to_s3(img_data, key)

        # Create and save thumbnail
        thumb_data = make_thumbnail(img_data)
        thumb_key = f'images/{name}_thumbnail.png'
        thumb_metadata = save_to_s3(thumb_data, thumb_key)
        metadata['thumbnail'] = thumb_metadata

        # Get image labels and add to metadata
        label_data = get_image_labels(metadata)
        metadata['label_info'] = label_data

        # Get a relevant XKCD image
        labels = [l['Name'] for l in label_data['Labels']]
        xkcd_results = search_xkcd(*labels)

        # Create an HTML file for the post
        context = {
            'labels': labels,
            'name': name,
            'xkcds': xkcd_results,
            'url': key
        }
        html = make_html(context).encode('utf-8')
        key = f'{name}.html'
        save_to_s3(html, key, content_type='text/html')
        return metadata
    except Exception as ex:
        logging.exception(ex)
        return {'error': str(ex)}


@app.route('/')
def index():
    """
    Render a list of available posts, as an HTML page.
    """
    posts = get_posts()
    context = {'name': 'Home', 'posts': posts}
    return Response(
        make_html(context, 'index.html'),
        headers={'Content-Type': 'text/html'})
