import json
import threading
import importlib
import six
from werkzeug.http import parse_options_header
from django.conf import settings
from django.http import HttpResponse
from gripcontrol import HttpStreamFormat
from django_grip import publish

try:
	from urllib import quote
except ImportError:
	from urllib.parse import quote

tlocal = threading.local()

# return dict of (channel, last-id)
def parse_grip_last(s):
	parsed = parse_options_header(s, multiple=True)

	out = {}
	for n in range(0, len(parsed), 2):
		channel = parsed[n]
		params = parsed[n + 1]
		last_id = params.get('last-id')
		if last_id is None:
			raise ValueError('channel "%s" has no last-id param' % channel)
		out[channel] = last_id
	return out

# return dict of (channel, last-id)
def parse_last_event_id(s):
	out = {}
	parts = s.split(',')
	for part in parts:
		channel, last_id = part.split(':')
		out[channel] = last_id
	return out

def make_id(ids):
	id_parts = []
	for channel, id in six.iteritems(ids):
		enc_channel = quote(channel)
		id_parts.append('%s:%s' % (enc_channel, id))
	return ','.join(id_parts)

def sse_encode_event(event_type, data, event_id=None):
	out = 'event: %s\n' % event_type
	if event_id:
		out += 'id: %s\n' % event_id
	out += 'data: %s\n\n' % json.dumps(data)
	return out

def sse_error_response(condition, text, extra={}):
	data = {'condition': condition, 'text': text}
	for k, v in six.iteritems(extra):
		data[k] = v
	body = sse_encode_event('stream-error', data, event_id='error')
	return HttpResponse(body, content_type='text/event-stream')

def publish_event(channel, event_type, data, pub_id, pub_prev_id,
		skip_user_ids=[]):
	if pub_id:
		event_id = '%I'
	else:
		event_id = None
	content = sse_encode_event(event_type, data, event_id=event_id)
	meta = {}
	if skip_user_ids:
		meta['skip_users'] = ','.join(skip_user_ids)
	publish(
		'events-%s' % channel,
		HttpStreamFormat(content),
		id=pub_id,
		prev_id=pub_prev_id,
		meta=meta)

def publish_kick(user_id, channel):
	msg = 'Permission denied to channels: %s' % channel
	data = {'condition': 'forbidden', 'text': msg, 'channels': [channel]}
	content = sse_encode_event('stream-error', data, event_id='error')
	meta = {'require_sub': 'events-%s' % channel}
	publish(
		'user-%s' % user_id,
		HttpStreamFormat(content),
		id='kick-1',
		meta=meta)
	publish(
		'user-%s' % user_id,
		HttpStreamFormat(close=True),
		id='kick-2',
		prev_id='kick-1',
		meta=meta)

def load_class(name):
	at = name.rfind('.')
	if at == -1:
		raise ValueError('class name contains no \'.\'')
	module_name = name[0:at]
	class_name = name[at + 1:]
	return getattr(importlib.import_module(module_name), class_name)()

# load and keep in thread local storage
def get_class(name):
	if not hasattr(tlocal, 'loaded'):
		tlocal.loaded = {}
	c = tlocal.loaded.get(name)
	if c is None:
		c = load_class(name)
		tlocal.loaded[name] = c
	return c

def get_class_from_setting(setting_name, default=None):
	if hasattr(settings, setting_name):
		return get_class(getattr(settings, setting_name))
	elif default:
		return get_class(default)
	else:
		return None

def get_storage():
	return get_class_from_setting('EVENTSTREAM_STORAGE_CLASS')

def get_authorizer():
	return get_class_from_setting(
		'EVENTSTREAM_AUTHORIZER_CLASS',
		'django_eventstream.authorizer.DefaultAuthorizer')
