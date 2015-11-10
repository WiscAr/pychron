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
import md5
import os
import pickle

from envisage.ui.tasks.task_extension import TaskExtension
from envisage.ui.tasks.task_factory import TaskFactory
from pyface.tasks.action.schema import SMenu, SGroup
from pyface.tasks.action.schema_addition import SchemaAddition


# ============= standard library imports ========================
# ============= local library imports  ==========================
from pychron.envisage.browser.interpreted_age_browser_model import InterpretedAgeBrowserModel
from pychron.envisage.tasks.base_task_plugin import BaseTaskPlugin
from pychron.paths import paths, get_file_text
from pychron.pipeline.tasks.actions import ConfigureRecallAction, IdeogramAction, IsochronAction, SpectrumAction, \
    SeriesAction, BlanksAction, ICFactorAction, ResetFactoryDefaultsAction, VerticalFluxAction
from pychron.pipeline.tasks.browser_task import BrowserTask
from pychron.pipeline.tasks.preferences import PipelinePreferencesPane
from pychron.pipeline.tasks.task import PipelineTask
from pychron.envisage.browser.sample_browser_model import SampleBrowserModel


class PipelinePlugin(BaseTaskPlugin):
    def _file_defaults_default(self):
        ov = True
        files = [['pipeline_template_file', 'PIPELINE_TEMPLATES', ov],
                 ['icfactor_template', 'ICFACTOR', ov],
                 ['blanks_template', 'BLANKS', ov],
                 ['iso_evo_template', 'ISOEVO', ov],
                 ['ideogram_template', 'IDEO', ov],
                 ['spectrum_template', 'SPEC', ov],
                 ['isochron_template', 'ISOCHRON', ov],
                 ['csv_ideogram_template', 'CSV_IDEO', ov],
                 ['vertical_flux_template', 'VERTICAL_FLUX', ov],
                 ['analysis_table_template', 'ANALYSIS_TABLE', ov],
                 ['interpreted_age_table_template', 'INTERPRETED_AGE_TABLE', ov]]

        # open the manifest file to set the overwrite flag
        if os.path.isfile(paths.template_manifest_file):
            with open(paths.template_manifest) as rfile:
                manifest = pickle.load(rfile)
        else:
            manifest = {}

        for item in files:
            fn, t, o = item
            txt = get_file_text(t)
            h = md5.hash(txt)
            if fn in manifest and h == manifest[fn]:
                item[2] = False

            manifest[fn] = h

        with open(paths.template_manifest_file) as wfile:
            pickle.dump(manifest, wfile)

    def _pipeline_factory(self):
        model = self.application.get_service(SampleBrowserModel)
        iamodel = self.application.get_service(InterpretedAgeBrowserModel)
        t = PipelineTask(browser_model=model,
                         interpreted_age_browser_model=iamodel)
        return t

    def _browser_factory(self):
        model = self.application.get_service(SampleBrowserModel)
        t = BrowserTask(browser_model=model)
        return t

    def _browser_model_factory(self):
        return SampleBrowserModel(application=self.application)

    def _interpreted_age_browser_model_factory(self):
        return InterpretedAgeBrowserModel(application=self.application)

    # defaults
    def _service_offers_default(self):
        so = self.service_offer_factory(protocol=SampleBrowserModel,
                                        factory=self._browser_model_factory)

        so1 = self.service_offer_factory(protocol=InterpretedAgeBrowserModel,
                                         factory=self._interpreted_age_browser_model_factory)
        return [so, so1]

    def _preferences_panes_default(self):
        return [PipelinePreferencesPane]

    def _task_extensions_default(self):
        def data_menu():
            return SMenu(id='data.menu', name='Data')

        def plot_group():
            return SGroup(id='plot.group')

        def reduction_group():
            return SGroup(id='reduction.group')

        pg = 'MenuBar/data.menu/plot.group'
        rg = 'MenuBar/data.menu/reduction.group'
        plotting_actions = [SchemaAddition(factory=data_menu,
                                           path='MenuBar',
                                           before='tools.menu',
                                           after='view.menu', ),
                            SchemaAddition(factory=plot_group,
                                           path='MenuBar/data.menu'),
                            SchemaAddition(factory=IdeogramAction,
                                           path=pg),
                            SchemaAddition(factory=SpectrumAction,
                                           path=pg),
                            SchemaAddition(factory=IsochronAction,
                                           path=pg),
                            SchemaAddition(factory=SeriesAction,
                                           path=pg),
                            SchemaAddition(factory=VerticalFluxAction,
                                           path=pg)]

        reduction_actions = [SchemaAddition(factory=reduction_group,
                                            path='MenuBar/data.menu'),
                             SchemaAddition(factory=BlanksAction,
                                            path=rg),
                             SchemaAddition(factory=ICFactorAction,
                                            path=rg)]
        help_actions = [SchemaAddition(factory=ResetFactoryDefaultsAction,
                                       path='MenuBar/help.menu')]
        configure_recall = SchemaAddition(factory=ConfigureRecallAction,
                                          path='MenuBar/Edit')
        browser_actions = [configure_recall]

        return [TaskExtension(task_id='pychron.pipeline.task',
                              actions=[configure_recall]),
                TaskExtension(task_id='pychron.browser.task',
                              actions=browser_actions),
                TaskExtension(actions=plotting_actions + reduction_actions + help_actions)]

    def _tasks_default(self):
        return [TaskFactory(id='pychron.pipeline.task',
                            name='Pipeline',
                            accelerator='Ctrl+p',
                            factory=self._pipeline_factory),
                TaskFactory(id='pychron.browser.task',
                            name='Browser',
                            accelerator='Ctrl+b',
                            factory=self._browser_factory)]

# ============= EOF =============================================
