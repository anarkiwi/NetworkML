import json
import os
import re
import socket

import pika


class ResultsOutput:

    def __init__(self, logger, version, use_rabbit):
        self.logger = logger
        self.version = version
        self.use_rabbit = use_rabbit
        self.rabbit_host = os.getenv('RABBIT_HOST', 'messenger')
        self.rabbit_queue_name = os.getenv('RABBIT_QUEUE_NAME', 'task_queue')
        self.rabbit_exchange = os.getenv('RABBIT_EXCHANGE', 'task_queue')
        self.rabbit_port = int(os.getenv('RABBIT_PORT', '5672'))

    def connect_rabbit(self):
        params = pika.ConnectionParameters(host=self.rabbit_host, port=self.rabbit_port)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(queue=self.rabbit_queue_name, durable=True)
        return channel

    def send_rabbit_msg(self, msg, channel):
        body = json.dumps(msg)
        channel.basic_publish(exchange=self.rabbit_exchange,
                              routing_key=self.rabbit_queue_name,
                              body=body,
                              properties=pika.BasicProperties(delivery_mode=2))
        self.logger.info('send_rabbit_msg: %s', body)

    def rabbit_msg_template(self, uid, file_path, result):
        return {
            'id': uid,
            'type': 'metadata',
            'file_path': file_path,
            'data': result,
            'results': {
                'tool': 'networkml',
                'version': self.version}}

    @staticmethod
    def parse_pcap_name(base_pcap):
        # The parsing operation below assumes a specific file naming
        # convention trace_DeviceName-deviceID-time-duration-flags.pcap
        # Explanation: All files coming from Poseidon have trace_ at their
        # beginning. The device name and deviceID colums are self explanatory.
        # Time refers to the day of the week and time of day. Duration refers
        # to the length of the network traffic capture. The flags aspect
        # refers to an unknown characteristic.
        # TODO: tolerate tshark labels in the trace name, but do not parse them for now.
        pcap_key = None
        pcap_labels = None
        if base_pcap.startswith('trace_'):
            for pcap_re, key_pos, label_pos in (
                    (re.compile(
                        r'^trace_([\da-f]+)_([0-9\_\-]+)-(client|server)-(.+).pcap$'), 1, 4),
                    (re.compile(r'^trace_([\da-f]+)_([0-9\_\-]+).pcap$'), 1, None)):
                pcap_match = pcap_re.match(base_pcap)
                if pcap_match:
                    pcap_key = pcap_match.group(key_pos)
                    if label_pos:
                        pcap_labels = pcap_match.group(label_pos)
                    break
        else:
            # Not a Poseidon trace file, return basename as key.
            pcap_key = base_pcap.split('.')[0]
        return (pcap_key, pcap_labels)

    def results_template(self, file_path, valid, results):
        base_pcap = os.path.basename(file_path)
        pcap_key, _ = self.parse_pcap_name(base_pcap)
        base_results = {'valid': valid}
        base_results.update(results)
        return {pcap_key: base_results, 'pcap': base_pcap}

    def output_msg(self, uid, file_path, result):
        if not self.use_rabbit:
            return

        try:
            channel = self.connect_rabbit()
            msg = self.rabbit_msg_template(uid, file_path, result)
            self.send_rabbit_msg(msg, channel)
            msg = self.rabbit_msg_template(uid, file_path, '')
            self.send_rabbit_msg(msg, channel)
        except (socket.gaierror, pika.exceptions.AMQPConnectionError) as err:
            self.logger.error(f'Failed to send Rabbit message because: {err}')

    def output_invalid(self, uid, file_path):
        self.output_msg(
            uid, file_path, self.results_template(file_path, False, {}))

    @staticmethod
    def valid_template(timestamp, source_ip, source_mac,
                       behavior, investigate, labels, confidences,
                       pcap_labels):
        return {
            'decisions': {
                'behavior': behavior,
                'investigate': investigate,
            },
            'classification': {
                'labels': labels,
                'confidences': confidences,
            },
            'timestamp': timestamp,
            'source_ip': source_ip,
            'source_mac': source_mac,
            'pcap_labels': pcap_labels,
        }

    def output_valid(self, uid, file_path, timestamp, source_ip, source_mac,
                     labels, confidences,
                     behavior='normal', investigate=False, pcap_labels=None):
        self.output_msg(uid, file_path, self.results_template(
            file_path, True, self.valid_template(
                timestamp, source_ip, source_mac,
                behavior, investigate, labels, confidences,
                pcap_labels)))
