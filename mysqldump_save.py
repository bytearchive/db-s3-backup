import subprocess
from boto.s3.connection import S3Connection
from boto.s3.key import Key
from datetime import datetime, timedelta
import random, string
import os
import re

intervals = [
	# For 2 days, 1 backup per 1 hour
	(timedelta(days=2), timedelta(hours=1)),
	# For 7 days, 1 backup per 1 day
	(timedelta(days=7), timedelta(days=1)),
	# For 30 days, 1 backup per 3 day
	(timedelta(days=30), timedelta(days=3)),
	# For 90 days, 1 backup per 7 days
	(timedelta(days=90), timedelta(days=7)),
	# Forever, 1 backup for 30 days
	(None, timedelta(days=30)),
]

mysqldump_regex = re.compile(r'mysqldump_(.*?)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(\d+)_(.*?)\.sql')

# Connect to Amazon S3

def connect_to_s3(aws_config, verbose = False):
	if verbose:
		print('Connecting to Amazon S3')
	
	s3_connection = S3Connection(aws_access_key_id=aws_config['AWS_ACCESS_KEY_ID'], aws_secret_access_key=aws_config['AWS_SECRET_ACCESS_KEY'])
	s3_bucket = s3_connection.get_bucket(aws_config['AWS_STORAGE_BUCKET_NAME'])
	
	if verbose:
		print('+ Connected')
	
	return (s3_connection, s3_bucket,)

# 
# Create MySQL dump
# 

def create_mysql_dump(mysql_config, s3_bucket, s3_bucket_key_name, filepath, verbose = False):
	sqldump_cmd = ['mysqldump', mysql_config['NAME'], '-h', mysql_config['HOST'], '-P', mysql_config['PORT'], '-u', mysql_config['USER'], '-p{password}'.format(password=mysql_config['PASSWORD'])]

	proc = subprocess.Popen(sqldump_cmd, stdout=subprocess.PIPE)

	# Write to file
	
	if verbose:
		print('Dumping MySQL database: {database} to file {filepath}'.format(database=mysql_config['NAME'], filepath=filepath))

	f=open(filepath, 'w+')
	i=0
	while True:
		buf = proc.stdout.read(4096*1024) # Read 4 MB
		if buf != '':
			f.write(buf)
			if verbose:
				print('- Written 4 MB')
		else:
			break
	
	if verbose:
		print('+ Dump finished')

	# Send file to S3
	
	s3_file = Key(s3_bucket, name=s3_bucket_key_name)

	if verbose:
		print('Uploading dump to Amazon S3: {url}'.format(url=s3_file.generate_url(expires_in=0, query_auth=False)))

	s3_file.set_contents_from_file(f)
	
	if verbose:
		print('+ Upload finished')

	f.close()

# 
# Cleanup old S3 backups
# 

def cleanup_old_backups(mysql_config, intervals, s3_bucket, verbose = False, action = True):
	backups=[]
	now=datetime.now()

	
	for key in s3_bucket.list():
		m=mysqldump_regex.match(key.name)
		if m and m.group(1) == mysql_config['NAME']:
			d=datetime(int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), int(m.group(6)), int(m.group(7)))
		
			backups.append((key, now-d))

	backups.sort(key=lambda (key, age,): age, reverse=False)
	
	if verbose:
		print('Found {count} backups on Amazon S3'.format(count=len(backups)))
		print('Deleting old backups on Amazon S3')

	oldest_age = None

	for (key, age) in backups:
		if not oldest_age:
			oldest_age = age
			if verbose:
				print('+ Keep {key}'.format(key=key.name))
			continue
	
		interval = None
		for (saving_duration, saving_interval) in intervals:
			if saving_duration == None or age < saving_duration:
				interval = saving_interval
				break
	
		# Keep this backup
		if age - oldest_age > saving_interval:
			if verbose:
				print('+ Keep {key}'.format(key=key.name))
			oldest_age = age
		# Delete backup
		else:
			if verbose:
				print('- Delete {key}'.format(key=key.name))
			if action:
				key.delete()

# 
# Delete local backups
# 

def delete_local_backups(dir_path, verbose = False, action = True):
	files = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path,f)) and mysqldump_regex.match(f)]
	print('Deleting local backups')
	for file in files:
		filepath = os.path.join(dir_path, file)
		if verbose:
			print('- Delete {filepath}'.format(filepath=filepath))
		if action:
			os.remove(filepath)

# 
# Run as Main + Option Parser
# 

if __name__ == '__main__':
	import argparse, json
	
	parser = argparse.ArgumentParser(description='MySQL S3 Dump Tool')
	parser.add_argument(
		dest='config_file',
		help='Backup JSON configuration file.',
	)
	parser.add_argument(
		'-c'
		'--create_dump',
		action='store_true',
		dest='create_dump',
		help='Creates a MySQL dump and uploads it to Amazon S3.',
		default=False,
	)
	parser.add_argument(
		'--delete-remote',
		action='store_true',
		dest='delete_remote',
		help='Delete old backups on Amazon S3.',
		default=False,
	)
	parser.add_argument(
		'--delete-local',
		action='store_true',
		dest='delete_local',
		help='Delete old local backups.',
		default=False,
	)
	parser.add_argument(
		'--simulate-delete',
		action='store_true',
		dest='simulate_delete',
		help='Simulate old backups deletion on Amazon S3 or locally.',
		default=False,
	)
	parser.add_argument(
		'-v'
		'--verbose',
		action='store_true',
		dest='verbose',
		help='Verbose mode.',
		default=False,
	)
	args = parser.parse_args()
	
	# Loads configuration file from JSON
	try:
		f=open(args.config_file, 'r')
	except Exception, e:
		print('Cannot open configuration file file ({filepath}).'.format(filepath=args.config_file))
		exit(1)
	try:
		config=json.loads(f.read())
	except Exception, e:
		print('Cannot parse configuration file (must be JSON).')
		exit(2)
	f.close()
	
	# Generate name/path/now
	filename = 'mysqldump_{database}_{datetime:%Y}_{datetime:%m}_{datetime:%d}_{datetime:%H}_{datetime:%M}_{datetime:%S}_{random}.sql'.format(datetime=datetime.now(), database=config['mysql']['NAME'], random=''.join([random.choice(string.letters+string.digits) for x in range(5)]))
	backup_dir = os.path.expanduser('~/mysqldump')
	filepath = os.path.join(backup_dir, filename)
	
	# Run scripts...
	
	(s3_connection, s3_bucket,) = connect_to_s3(config['aws'], verbose=args.verbose)
	
	if args.create_dump:
		create_mysql_dump(config['mysql'], s3_bucket, filename, filepath, verbose=args.verbose)
	
	if args.delete_remote:
		cleanup_old_backups(config['mysql'], intervals, s3_bucket, verbose=args.verbose, action = (not args.simulate_delete))
	
	if args.delete_local:
		delete_local_backups(backup_dir, verbose = args.verbose, action = (not args.simulate_delete))



