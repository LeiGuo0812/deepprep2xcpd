"""Test shell argument preservation and mount access without a Docker daemon."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'source with spaces $(literal)'
        self.results=self.root/'results with spaces'
        self.bin=self.root/'bin'
        for p in (self.source,self.results,self.bin):p.mkdir()
        self.capture=self.root/'args.json'
        fake=self.bin/'docker'
        fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ["LAUNCHER_TEST_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))\n')
        fake.chmod(0o755)
        self.script=Path(__file__).resolve().parents[1]/'scripts/docker_adapter.sh'
        self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                      DEEPPREP_DIR=str(self.source),ADAPTER_RESULTS=str(self.results),
                      LAUNCHER_TEST_CAPTURE=str(self.capture))
        for key in ('ADAPTER_IMAGE','ADAPTER_PLATFORM','BIDS_DIR','EXTRA_WORK_DIR'):
            self.env.pop(key,None)

    def launch(self,*args):
        p=subprocess.run(['bash',str(self.script),*args],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        return json.loads(self.capture.read_text())

    def test_plan_preserves_spaces_and_uses_read_only_source(self):
        args=self.launch('plan','--manifest','/result/manifest with spaces.json')
        self.assertIn(str(self.source)+':/deepprep:ro',args)
        self.assertIn(str(self.results)+':/result:rw',args)
        self.assertEqual(args[-3:],['plan','--manifest','/result/manifest with spaces.json'])

    def test_only_inplace_and_rollback_enable_source_writes(self):
        for command in [('convert','--layout','inplace'),('convert','--layout=inplace'),
                        ('rollback','--transaction','example')]:
            with self.subTest(command=command):
                self.assertIn(str(self.source)+':/deepprep:rw',self.launch(*command))
        for command in [('convert','--layout','separate'),('validate',)]:
            with self.subTest(command=command):
                self.assertIn(str(self.source)+':/deepprep:ro',self.launch(*command))

    def test_test_command_needs_no_source_mount_and_disables_network(self):
        self.env.pop('DEEPPREP_DIR');self.env.pop('ADAPTER_RESULTS')
        args=self.launch('test')
        self.assertIn('--network',args)
        self.assertEqual(args[args.index('--network')+1],'none')
        self.assertEqual(args[-6:],['-m','unittest','discover','-s','/adapter/tests','-v'])

    def test_extra_sources_and_offline_image_and_platform(self):
        self.env.update(BIDS_DIR=str(self.source),EXTRA_WORK_DIR=str(self.results),
                        ADAPTER_IMAGE='offline-runtime:26.2.0',ADAPTER_PLATFORM='linux/amd64')
        args=self.launch('plan')
        self.assertIn(str(self.source)+':/bids:ro',args)
        self.assertIn(str(self.results)+':/extra_work:ro',args)
        self.assertIn('offline-runtime:26.2.0',args)
        self.assertEqual(args[args.index('--platform')+1],'linux/amd64')

    def test_missing_source_fails_before_docker_is_called(self):
        self.env['DEEPPREP_DIR']=str(self.root/'does-not-exist')
        p=subprocess.run(['bash',str(self.script),'plan'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,2)
        self.assertFalse(self.capture.exists())
        self.assertIn('Directory does not exist',p.stderr)


if __name__=='__main__':unittest.main()
