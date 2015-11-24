# ===============================================================================
# Copyright 2015 Jake Ross
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ===============================================================================

# ============= enthought library imports =======================
from traits.api import Instance, Bool
# ============= standard library imports ========================
import base64
import hashlib
import os
import struct
from datetime import datetime
from uncertainties import std_dev, nominal_value
# ============= local library imports  ==========================
from pychron.dvc import jdump
from pychron.dvc.dvc_analysis import META_ATTRS, EXTRACTION_ATTRS, analysis_path, PATH_MODIFIERS
from pychron.experiment.automated_run.persistence import BasePersister
from pychron.experiment.classifier.isotope_classifier import IsotopeClassifier
from pychron.git_archive.repo_manager import GitRepoManager
from pychron.paths import paths
from pychron.pychron_constants import DVC_PROTOCOL


def format_experiment_identifier(project):
    return project.replace('/', '_').replace('\\', '_')


class DVCPersister(BasePersister):
    experiment_repo = Instance(GitRepoManager)
    dvc = Instance(DVC_PROTOCOL)
    use_isotope_classifier = Bool(False)
    isotope_classifier = Instance(IsotopeClassifier, ())

    def per_spec_save(self, pr, commit=False, msg_prefix=None):
        self.per_spec = pr
        self.initialize(False)
        self.pre_extraction_save()
        self.pre_measurement_save()
        self.post_extraction_save('', '', None)
        self.post_measurement_save(commit=commit, msg_prefix=msg_prefix)

    def initialize(self, experiment, pull=True):
        """
        setup git repos.

        repositories are guaranteed to exist. The automated run factory clones the required projects
        on demand.

        :return:
        """
        self.debug('^^^^^^^^^^^^^ Initialize DVCPersister {} pull={}'.format(experiment, pull))

        self.dvc.initialize()

        experiment = format_experiment_identifier(experiment)
        self.experiment_repo = repo = GitRepoManager()

        root = os.path.join(paths.experiment_dataset_dir, experiment)
        repo.open_repo(root)

        remote = 'origin'
        if repo.has_remote(remote) and pull:
            self.info('pulling changes from experiment repo: {}'.format(experiment))
            self.experiment_repo.pull(remote=remote, use_progress=False)

    def pre_extraction_save(self):
        pass

    def post_extraction_save(self, rblob, oblob, snapshots):
        p = self._make_path(modifier='extraction')

        if rblob:
            rblob = base64.b64encode(rblob[0])
        if oblob:
            oblob = base64.b64encode(oblob[0])

        obj = {'request': rblob,
               'response': oblob}

        for e in EXTRACTION_ATTRS:
            v = getattr(self.per_spec.run_spec, e)
            obj[e] = v

        ps = []
        for i, pp in enumerate(self.per_spec.positions):
            pos, x, y, z = None, None, None, None
            if isinstance(pp, tuple):
                if len(pp) == 2:
                    x, y = pp
                elif len(pp) == 3:
                    x, y, z = pp

            else:
                pos = pp
                try:
                    ep = self.per_spec.extraction_positions[i]
                    x = ep[0]
                    y = ep[1]
                    if len(ep) == 3:
                        z = ep[2]
                except IndexError:
                    self.debug('no extraction position for {}'.format(pp))
            pd = {'x': x, 'y': y, 'z': z, 'position': pos, 'is_degas': self.per_spec.run_spec.identifier == 'dg'}
            ps.append(pd)

        obj['positions'] = ps
        hexsha = self.dvc.get_meta_head()
        obj['commit'] = str(hexsha)

        jdump(obj, p)

    def pre_measurement_save(self):
        pass

    def _save_peak_center(self, pc):
        self.info('DVC saving peakcenter')
        p = self._make_path(modifier='peakcenter')
        obj = {}
        if pc:
            obj['reference_detector'] = pc.reference_detector
            obj['reference_isotope'] = pc.reference_isotope
            if pc.result:
                xs, ys, _mx, _my = pc.result
                obj.update({'low_dac': xs[0],
                            'center_dac': xs[1],
                            'high_dac': xs[2],
                            'low_signal': ys[0],
                            'center_signal': ys[1],
                            'high_signal': ys[2]})

            data = pc.get_data()
            if data:
                fmt = '>ff'
                obj['fmt'] = fmt
                for det, pts in data:
                    obj[det] = base64.b64encode(''.join([struct.pack(fmt, *di) for di in pts]))

        jdump(obj, p)

    def post_measurement_save(self, commit=True, msg_prefix='Collection'):
        """
        save
            - analysis.json
            - analysis.monitor.json

        check if unique spectrometer.json
        commit changes
        push changes
        :return:
        """
        self.debug('================= post measurement started')
        # save spectrometer
        spec_sha = self._get_spectrometer_sha()
        spec_path = os.path.join(self.experiment_repo.path, '{}.json'.format(spec_sha))
        if not os.path.isfile(spec_path):
            self._save_spectrometer_file(spec_path)

        self.dvc.meta_repo.save_gains(self.per_spec.run_spec.mass_spectrometer,
                                      self.per_spec.gains)

        # save analysis
        t = datetime.now()
        self._save_analysis(timestamp=t, spec_sha=spec_sha)

        self._save_analysis_db(t)

        # save monitor
        self._save_monitor()

        # save peak center
        self._save_peak_center(self.per_spec.peak_center)

        # stage files
        paths = [spec_path, ] + [self._make_path(modifier=m) for m in PATH_MODIFIERS]

        for p in paths:
            if os.path.isfile(p):
                self.experiment_repo.add(p, commit=False, msg_prefix=msg_prefix)
            else:
                self.debug('not at valid file {}'.format(p))

        if commit:
            self.experiment_repo.smart_pull(accept_their=True)

            # commit files
            self.experiment_repo.commit('added analysis {}'.format(self.per_spec.run_spec.runid))

            # update meta
            self.dvc.meta_pull(accept_our=True)

            self.dvc.meta_commit('repo updated for analysis {}'.format(self.per_spec.run_spec.runid))

            # push commit
            self.dvc.meta_push()

        self.debug('================= post measurement finished')

    # private
    def _save_analysis_db(self, timestamp):
        rs = self.per_spec.run_spec
        d = {k: getattr(rs, k) for k in ('uuid', 'analysis_type', 'aliquot',
                                         'increment', 'mass_spectrometer',
                                         'extract_device', 'weight', 'comment',
                                         'cleanup', 'duration', 'extract_value', 'extract_units')}

        if not self.per_spec.timestamp:
            d['timestamp'] = timestamp
        else:
            d['timestamp'] = self.per_spec.timestamp

        # save script names
        d['measurementName'] = self.per_spec.measurement_name
        d['extractionName'] = self.per_spec.extraction_name

        db = self.dvc.db
        with db.session_ctx():
            an = db.add_analysis(**d)

            # all associations are handled by the ExperimentExecutor._retroactive_experiment_identifiers

            # # special associations are handled by the ExperimentExecutor._retroactive_experiment_identifiers
            # if not is_special(rs.runid):
            if self.per_spec.use_experiment_association:
                self.dvc.add_experiment_association(rs.experiment_identifier, rs)

            pos = db.get_identifier(rs.identifier)
            an.irradiation_position = pos
            t = self.per_spec.tag
            if t:
                dbtag = db.get_tag(t)
                if not dbtag:
                    dbtag = db.add_tag(name=t)

            db.flush()
            an.change.tag_item = dbtag

            change = db.add_analysis_change(tag=t)
            an.change = change
            # an.change.tag_item = dbtag
            # self._save_measured_positions()

    def _save_measured_positions(self):
        dvc = self.dvc

        load_name = self.per_spec.load_name
        for i, pp in enumerate(self.per_spec.positions):
            if isinstance(pp, tuple):
                if len(pp) > 1:
                    if len(pp) == 3:
                        dvc.add_measured_position('', load_name, x=pp[0], y=pp[1], z=pp[2])
                    else:
                        dvc.add_measured_position('', load_name, x=pp[0], y=pp[1])
                else:
                    dvc.add_measured_position(pp[0], load_name)

            else:
                dbpos = dvc.add_measured_position(pp, load_name)
                try:
                    ep = self.per_spec.extraction_positions[i]
                    dbpos.x = ep[0]
                    dbpos.y = ep[1]
                    if len(ep) == 3:
                        dbpos.z = ep[2]
                except IndexError:
                    self.debug('no extraction position for {}'.format(pp))

    def _make_analysis_dict(self, keys=None):
        rs = self.per_spec.run_spec
        if keys is None:
            keys = META_ATTRS

        d = {k: getattr(rs, k) for k in keys}
        return d

    def _save_analysis(self, timestamp, **kw):

        isos = {}
        dets = {}
        signals = []
        baselines = []
        sniffs = []
        blanks = {}
        intercepts = {}
        cbaselines = {}
        icfactors = {}

        if self.use_isotope_classifier:
            clf = self.isotope_classifier

        endianness = '>'
        for iso in self.per_spec.arar_age.isotopes.values():

            sblob = base64.b64encode(iso.pack(endianness, as_hex=False))
            snblob = base64.b64encode(iso.sniff.pack(endianness, as_hex=False))
            signals.append({'isotope': iso.name, 'detector': iso.detector, 'blob': sblob})
            sniffs.append({'isotope': iso.name, 'detector': iso.detector, 'blob': snblob})

            isod = {'detector': iso.detector}
            if self.use_isotope_classifier:
                klass, prob = clf.predict_isotope(iso)
                isod.update(classification=klass,
                            classification_probability=prob)

            isos[iso.name] = isod
            if iso.detector not in dets:
                bblob = base64.b64encode(iso.baseline.pack(endianness, as_hex=False))
                baselines.append({'detector': iso.detector, 'blob': bblob})
                dets[iso.detector] = {'deflection': self.per_spec.defl_dict.get(iso.detector),
                                      'gain': self.per_spec.gains.get(iso.detector)}

                icfactors[iso.detector] = {'value': float(nominal_value(iso.ic_factor)),
                                           'error': float(std_dev(iso.ic_factor)),
                                           'fit': 'default',
                                           'references': []}
                cbaselines[iso.detector] = {'fit': iso.baseline.fit,
                                            'value': float(nominal_value(iso.baseline.uvalue)),
                                            'error': float(std_dev(iso.baseline.uvalue))}

            intercepts[iso.name] = {'fit': iso.fit,
                                    'value': float(nominal_value(iso.uvalue)),
                                    'error': float(std_dev(iso.uvalue))}
            blanks[iso.name] = {'fit': 'previous',
                                'references': [{'runid': self.per_spec.previous_blank_runid,
                                                'exclude': False}],
                                'value': float(nominal_value(iso.blank.uvalue)),
                                'error': float(std_dev(iso.blank.uvalue))}

        obj = self._make_analysis_dict()

        from pychron.experiment import __version__ as eversion
        from pychron.dvc import __version__ as dversion

        if not self.per_spec.timestamp:
            obj['timestamp'] = timestamp.isoformat()
        else:
            obj['timestamp'] = self.per_spec.timestamp.isoformat()

        obj['collection_version'] = '{}:{}'.format(eversion, dversion)
        obj['detectors'] = dets
        obj['isotopes'] = isos
        obj.update(**kw)

        # save the scripts
        ms = self.per_spec.run_spec.mass_spectrometer
        for si in ('measurement', 'extraction'):
            name = getattr(self.per_spec, '{}_name'.format(si))
            blob = getattr(self.per_spec, '{}_blob'.format(si))
            self.dvc.meta_repo.update_script(ms, name, blob)

        # save experiment
        self.dvc.update_experiment_queue(ms, self.per_spec.experiment_queue_name,
                                         self.per_spec.experiment_queue_blob)

        hexsha = str(self.dvc.get_meta_head())
        obj['commit'] = hexsha

        # dump runid.json
        p = self._make_path()
        jdump(obj, p)

        p = self._make_path(modifier='intercepts')
        jdump(intercepts, p)

        # dump runid.blank.json
        p = self._make_path(modifier='blanks')
        jdump(blanks, p)

        p = self._make_path(modifier='baselines')
        jdump(cbaselines, p)

        p = self._make_path(modifier='icfactors')
        jdump(icfactors, p)

        # dump runid.data.json
        p = self._make_path(modifier='.data')
        data = {'commit': hexsha,
                'encoding': 'base64',
                'format': '{}ff'.format(endianness),
                'signals': signals, 'baselines': baselines, 'sniffs': sniffs}
        jdump(data, p)

    def _make_path(self, modifier=None, extension='.json'):
        runid = self.per_spec.run_spec.runid
        experiment_id = self.per_spec.run_spec.experiment_identifier
        return analysis_path(runid, experiment_id, modifier, extension, mode='w')

    def _get_spectrometer_sha(self):
        """
        return a sha-1 hash.

        generate using spec_dict, defl_dict, and gains
        spec_dict: source parameters, cdd operating voltage
        defl_dict: detector deflections
        gains: detector gains

        make hash using
        for key,value in dictionary:
            sha1.update(key)
            sha1.update(value)

        to ensure consistence, dictionaries are sorted by key
        for key,value in sorted(dictionary)
        :return:
        """
        sha = hashlib.sha1()
        for d in (self.per_spec.spec_dict, self.per_spec.defl_dict, self.per_spec.gains):
            for k, v in sorted(d.items()):
                sha.update(k)
                sha.update(str(v))

        return sha.hexdigest()

    def _save_monitor(self):
        if self.per_spec.monitor:
            p = self._make_path(modifier='monitor')
            checks = []
            for ci in self.per_spec.monitor.checks:
                data = ''.join([struct.pack('>ff', x, y) for x, y in ci.data])
                params = dict(name=ci.name,
                              parameter=ci.parameter, criterion=ci.criterion,
                              comparator=ci.comparator, tripped=ci.tripped,
                              data=data)
                checks.append(params)

            jdump(checks, p)

    def _save_spectrometer_file(self, path):
        obj = dict(spectrometer=dict(self.per_spec.spec_dict),
                   gains=dict(self.per_spec.gains),
                   deflections=dict(self.per_spec.defl_dict))
        hexsha = self.dvc.get_meta_head()
        obj['commit'] = str(hexsha)

        jdump(obj, path)

# ============= EOF =============================================