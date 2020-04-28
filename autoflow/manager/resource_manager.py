import datetime
import hashlib
import os
from copy import deepcopy
from getpass import getuser
from typing import Dict, Tuple, List, Union, Any

# import json5 as json
import peewee as pw
from frozendict import frozendict
from playhouse.fields import PickleField
from redis import Redis

from autoflow.ensemble.mean.regressor import MeanRegressor
from autoflow.ensemble.vote.classifier import VoteClassifier
from autoflow.manager.data_manager import DataManager
from autoflow.metrics import Scorer
from autoflow.utils.dict import update_data_structure
from autoflow.utils.hash import get_hash_of_Xy, get_hash_of_str, get_hash_of_dict
from autoflow.utils.klass import StrSignatureMixin
from autoflow.utils.logging_ import get_logger
from autoflow.utils.ml_task import MLTask
from generic_fs import FileSystem
from generic_fs.utils.db import get_db_class_by_db_type, get_JSONField, PickleField, create_database
from generic_fs.utils.fs import get_file_system


class ResourceManager(StrSignatureMixin):
    '''
    ``ResourceManager`` is a class manager computer resources such like ``file_system`` and ``data_base``.
    '''

    def __init__(
            self,
            store_path="~/autoflow",
            file_system="local",
            file_system_params=frozendict(),
            db_type="sqlite",
            db_params=frozendict(),
            redis_params=frozendict(),
            max_persistent_estimators=50,
            compress_suffix="bz2"

    ):
        '''

        Parameters
        ----------
        store_path: str
            A path store files, such as metadata and model file and database file, which belong to AutoFlow.
        file_system: str
            Indicator-string about which file system or storage system will be used.

            Available options list below:
                * ``local``
                * ``hdfs``
                * ``s3``

            ``local`` is default value.
        file_system_params: dict
            Specific file_system configuration.
        db_type: str
            Indicator-string about which file system or storage system will be used.

            Available options list below:
                * ``sqlite``
                * ``postgresql``
                * ``mysql``

            ``sqlite`` is default value.
        db_params: dict
            Specific database configuration.
        redis_params: dict
            Redis configuration.
        max_persistent_estimators: int
            Maximal number of models can persistent in single task.

            If more than this number, the The worst performing model file will be delete,

            the corresponding database record will also be deleted.
        compress_suffix: str
            compress file's suffix, default is bz2
        '''
        # --logger-------------------
        self.logger = get_logger(self)
        # --preprocessing------------
        file_system_params = dict(file_system_params)
        db_params = dict(db_params)
        redis_params = dict(redis_params)
        # ---file_system------------
        self.file_system_type = file_system
        self.file_system: FileSystem = get_file_system(file_system)(**file_system_params)
        if self.file_system_type == "local":
            store_path = os.path.expandvars(os.path.expanduser(store_path))
        self.store_path = store_path
        # ---data_base------------
        assert db_type in ("sqlite", "postgresql", "mysql")
        self.db_type = db_type
        self.db_params = dict(db_params)
        if db_type == "sqlite":
            assert self.file_system_type == "local"
        # ---redis----------------
        self.redis_params = dict(redis_params)
        # ---max_persistent_model---
        self.max_persistent_estimators = max_persistent_estimators
        # ---compress_suffix------------
        self.compress_suffix = compress_suffix
        # ---post_process------------
        self.store_path = store_path
        self.file_system.mkdir(self.store_path)
        self.is_init_experiments_db = False
        self.is_init_tasks_db = False
        self.is_init_hdls_db = False
        self.is_init_trials_db = False
        self.is_init_redis = False
        self.is_master = False
        # --some specific path based on file_system---
        self.datasets_dir = self.file_system.join(self.store_path, "datasets")
        self.databases_dir = self.file_system.join(self.store_path, "databases")
        self.parent_trials_dir = self.file_system.join(self.store_path, "trials")
        self.parent_experiments_dir = self.file_system.join(self.store_path, "experiments")
        for dir_path in [self.datasets_dir, self.databases_dir, self.parent_experiments_dir, self.parent_trials_dir]:
            self.file_system.mkdir(dir_path)
        # --db-----------------------------------------
        self.Datebase = get_db_class_by_db_type(self.db_type)
        # --JSONField-----------------------------------------
        self.JSONField = get_JSONField(self.db_type)
        # --database_name---------------------------------
        # None means didn't create database
        self._meta_records_db_name = None  # meta records database
        self._tasks_db_name = None

    def close_all(self):
        self.close_redis()
        self.close_experiments_table()
        self.close_tasks_table()
        self.close_hdls_table()
        self.close_trials_table()
        self.file_system.close_fs()

    def __reduce__(self):
        self.close_all()
        return super(ResourceManager, self).__reduce__()

    def update_db_params(self, database):
        db_params = deepcopy(self.db_params)
        if self.db_type == "sqlite":
            db_params["database"] = self.file_system.join(self.databases_dir, f"{database}.db")
        elif self.db_type == "postgresql":
            db_params["database"] = database
        elif self.db_type == "mysql":
            db_params["database"] = database
        else:
            raise NotImplementedError
        return db_params

    def forecast_new_id(self, Dataset, id_field):
        # fixme : 用来预测下一个自增主键的ID，但是感觉有问题
        try:
            records = Dataset.select(getattr(Dataset, id_field)). \
                order_by(-getattr(Dataset, id_field)). \
                limit(1)
            if len(records) == 0:
                estimated_id = 1
            else:
                estimated_id = getattr(records[0], id_field) + 1
        except Exception as e:
            self.logger.error(f"Database Error:\n{e}")
            estimated_id = 1
        return estimated_id

    def persistent_evaluated_model(self, info: Dict, model_id) -> Tuple[str, str, str, str]:
        y_info = {
            "y_true_indexes": info.get("y_true_indexes"),
            "y_preds": info.get("y_preds"),
            "y_test_pred": info.get("y_test_pred")
        }
        # ----dir---------------------
        self.trial_dir = self.file_system.join(self.parent_trials_dir, self.task_id, self.hdl_id)
        self.file_system.mkdir(self.trial_dir)
        # ----get specific URL---------
        models_path = self.file_system.join(self.trial_dir, f"{model_id}_models.{self.compress_suffix}")
        y_info_path = self.file_system.join(self.trial_dir, f"{model_id}_y-info.{self.compress_suffix}")
        if info["intermediate_result"] is not None:
            intermediate_result_path = self.file_system.join(self.trial_dir,
                                                             f"{model_id}_inter-res.{self.compress_suffix}")
        else:
            intermediate_result_path = ""
        if info["finally_fit_model"] is not None:
            finally_fit_model_path = self.file_system.join(self.trial_dir,
                                                           f"{model_id}_final.{self.compress_suffix}")
        else:
            finally_fit_model_path = ""
        # ----do dump---------------
        self.file_system.dump_pickle(info["models"], models_path)
        self.file_system.dump_pickle(y_info, y_info_path)
        if intermediate_result_path:
            self.file_system.dump_pickle(info["intermediate_result"], intermediate_result_path)
        if finally_fit_model_path:
            self.file_system.dump_pickle(info["finally_fit_model"], finally_fit_model_path)
        # ----return----------------
        return models_path, intermediate_result_path, finally_fit_model_path, y_info_path

    def get_ensemble_needed_info(self, task_id) -> Tuple[MLTask, Any, Any]:
        self.task_id = task_id
        self.init_tasks_table()
        task_record = self.TasksModel.select().where(self.TasksModel.task_id == task_id)[0]
        ml_task_str = task_record.ml_task
        ml_task = eval(ml_task_str)
        Xy_train_path = task_record.Xy_train_path
        Xy_train = self.file_system.load_pickle(Xy_train_path)
        Xy_test_path = task_record.Xy_test_path
        Xy_test = self.file_system.load_pickle(Xy_test_path)

        return ml_task, Xy_train, Xy_test

    def load_best_estimator(self, ml_task: MLTask):
        self.init_trials_table()
        record = self.TrialsModel.select() \
            .order_by(self.TrialsModel.loss, self.TrialsModel.cost_time).limit(1)[0]
        models = self.file_system.load_pickle(record.models_path)
        if ml_task.mainTask == "classification":
            estimator = VoteClassifier(models)
        else:
            estimator = MeanRegressor(models)
        return estimator

    def load_best_dhp(self):
        trial_id = self.get_best_k_trials(1)[0]
        record = self.TrialsModel.select().where(self.TrialsModel.trial_id == trial_id)[0]
        return record.dict_hyper_param

    def get_best_k_trials(self, k):
        self.init_trials_table()
        trial_ids = []
        records = self.TrialsModel.select().order_by(self.TrialsModel.loss, self.TrialsModel.cost_time).limit(k)
        for record in records:
            trial_ids.append(record.trial_id)
        return trial_ids

    def load_estimators_in_trials(self, trials: Union[List, Tuple]) -> Tuple[List, List, List]:
        self.init_trials_table()
        records = self.TrialsModel.select().where(self.TrialsModel.trial_id << trials)
        estimator_list = []
        y_true_indexes_list = []
        y_preds_list = []
        for record in records:
            exists = True
            if not self.file_system.exists(record.models_path):
                exists = False
            else:
                estimator_list.append(self.file_system.load_pickle(record.models_path))
            if exists:
                y_info = self.file_system.load_pickle(record.y_info_path)
                y_true_indexes_list.append(y_info["y_true_indexes"])
                y_preds_list.append(y_info["y_preds"])
        return estimator_list, y_true_indexes_list, y_preds_list

    def set_is_master(self, is_master):
        self.is_master = is_master

    # ----------runhistory------------------------------------------------------------------
    def get_runhistory_db_params(self, task_id):

        return self.update_db_params(self.get_tasks_db_name(task_id))

    @property
    def runhistory_db_params(self):
        return self.get_runhistory_db_params(self.task_id)

    def get_runhistory_table_name(self, hdl_id):
        return f"runhistory_{hdl_id}"

    @property
    def runhistory_table_name(self):
        return self.get_runhistory_table_name(self.hdl_id)

    def migrate_runhistory(self, old_task_id, old_hdl_id, new_task_id, new_hdl_id):
        from dsmac.runhistory.runhistory_db import RunHistoryDB
        # 1. 把 f"task_{old_task_id}".f"runhistory_{old_hdl_id}" 的所有数据records抽出来
        old_runhistory_db = RunHistoryDB(
            config_space=None,
            runhistory=None,
            db_type=self.db_type,
            db_params=self.get_runhistory_db_params(old_task_id),
            db_table_name=self.get_runhistory_table_name(old_hdl_id)
        ).get_model()
        new_runhistory_db = RunHistoryDB(
            config_space=None,
            runhistory=None,
            db_type=self.db_type,
            db_params=self.get_runhistory_db_params(new_task_id),
            db_table_name=self.get_runhistory_table_name(new_hdl_id)
        ).get_model()
        old_runhistory_records = old_runhistory_db.select().dicts()
        if len(old_runhistory_records) == 0:
            self.logger.warning(f"SMBO Transfer learning warning: old_runhistory_db have no records!")
        else:
            self.logger.info(f"SMBO Transfer learning will migrate {len(old_runhistory_records)} "
                             f"records from old_runhistory_records.")
            transfer_cnt = 0
            for record in old_runhistory_records:
                fetched = new_runhistory_db.select().where(new_runhistory_db.run_id == record["run_id"])
                if len(fetched) == 0:
                    transfer_cnt += 1
                    new_runhistory_db.create(**record)
                else:
                    self.logger.warning(f'''run_id '{record["run_id"]}' in new_runhistory_db is already exist.''')
            if transfer_cnt:
                self.logger.info(
                    f"SMBO Transfer learning successfully migrate {transfer_cnt} records to new_runhistory_db.")
            else:
                self.logger.warning(
                    f"Unfortunately, all the migrates failed, "
                    f"please check if you have done SMBO transfer learning in previous tasks.")

    # ----------database name------------------------------------------------------------------

    @property
    def meta_records_db_name(self):
        if self._meta_records_db_name is not None:
            return self._meta_records_db_name
        self._meta_records_db_name = "meta_records"
        create_database(self._meta_records_db_name, self.db_type, self.db_params)
        return self._meta_records_db_name

    def get_tasks_db_name(self, task_id):
        return f"task_{task_id}"

    @property
    def tasks_db_name(self):
        if self._tasks_db_name is not None:
            return self._tasks_db_name
        self._tasks_db_name = self.get_tasks_db_name(self.task_id)
        create_database(self._tasks_db_name, self.db_type, self.db_params)
        return self._tasks_db_name

    # ----------redis------------------------------------------------------------------

    # todo: 重构redis并增加测试与样例
    def connect_redis(self):
        if self.is_init_redis:
            return True
        try:
            self.redis_client = Redis(**self.redis_params)
            self.is_init_redis = True
            return True
        except Exception as e:
            self.logger.error(f"Redis Error:\n{e}")
            return False

    def close_redis(self):
        self.redis_client = None
        self.is_init_redis = False

    def clear_pid_list(self):
        self.redis_delete("pid_list")

    def push_pid_list(self):
        if self.connect_redis():
            self.redis_client.rpush("pid_list", os.getpid())

    def get_pid_list(self):
        if self.connect_redis():
            l = self.redis_client.lrange("pid_list", 0, -1)
            return list(map(lambda x: int(x.decode()), l))
        else:
            return []

    def redis_set(self, name, value, ex=None, px=None, nx=False, xx=False):
        if self.connect_redis():
            self.redis_client.set(name, value, ex, px, nx, xx)

    def redis_get(self, name):
        if self.connect_redis():
            return self.redis_client.get(name)
        else:
            return None

    def redis_delete(self, name):
        if self.connect_redis():
            self.redis_client.delete(name)

    # ----------experiments_model------------------------------------------------------------------
    def get_experiments_model(self) -> pw.Model:
        class Experiments(pw.Model):
            experiment_id = pw.AutoField(primary_key=True)
            general_experiment_timestamp = pw.DateTimeField(default=datetime.datetime.now)
            current_experiment_timestamp = pw.DateTimeField(default=datetime.datetime.now)
            hdl_id = pw.CharField(default="")
            task_id = pw.CharField(default="")
            hdl_constructors = self.JSONField(default=[])
            hdl_constructor = pw.TextField(default="")
            raw_hdl = self.JSONField(default={})
            hdl = self.JSONField(default={})
            tuners = self.JSONField(default=[])
            tuner = pw.TextField(default="")
            should_calc_all_metric = pw.BooleanField(default=True)
            data_manager_path = pw.TextField(default="")
            resource_manager_path = pw.TextField(default="")
            column_descriptions = self.JSONField(default={})
            column2feature_groups = self.JSONField(default={})
            dataset_metadata = self.JSONField(default={})
            metric = pw.CharField(default=""),
            splitter = pw.CharField(default="")
            ml_task = pw.CharField(default="")
            should_store_intermediate_result = pw.BooleanField(default=False)
            fit_ensemble_params = pw.TextField(default="auto"),
            should_finally_fit = pw.BooleanField(default=False)
            additional_info = self.JSONField(default={})  # trials与experiments同时存储
            user = pw.CharField(default=getuser)

            class Meta:
                database = self.experiments_db

        self.experiments_db.create_tables([Experiments])
        return Experiments

    def get_experiment_id_by_task_id(self, task_id):
        self.init_tasks_table()
        return self.TasksModel.select(self.TasksModel.experiment_id).where(self.TasksModel.task_id == task_id)[
            0].experiment_id

    def load_data_manager_by_experiment_id(self, experiment_id):
        self.init_experiments_table()
        experiment_id = int(experiment_id)
        record = self.ExperimentsModel.select().where(self.ExperimentsModel.experiment_id == experiment_id)[0]
        data_manager_path = record.data_manager_path
        data_manager = self.file_system.load_pickle(data_manager_path)
        return data_manager

    def insert_to_experiments_table(
            self,
            general_experiment_timestamp,
            current_experiment_timestamp,
            hdl_constructors,
            hdl_constructor,
            raw_hdl,
            hdl,
            tuners,
            tuner,
            should_calc_all_metric,
            data_manager,
            column_descriptions,
            dataset_metadata,
            metric,
            splitter,
            should_store_intermediate_result,
            fit_ensemble_params,
            additional_info,
            should_finally_fit,
            set_id=True
    ):
        self.close_all()
        copied_resource_manager = deepcopy(self)
        self.init_experiments_table()
        # estimate new experiment_id
        experiment_id = self.forecast_new_id(self.ExperimentsModel, "experiment_id")
        # todo: 是否需要删除data_manager的Xy
        data_manager = deepcopy(data_manager)
        data_manager.X_train = None
        data_manager.X_test = None
        data_manager.y_train = None
        data_manager.y_test = None
        self.experiment_dir = self.file_system.join(self.parent_experiments_dir, str(experiment_id))
        self.file_system.mkdir(self.experiment_dir)
        data_manager_path = self.file_system.join(self.experiment_dir, f"data_manager.{self.compress_suffix}")
        resource_manager_path = self.file_system.join(self.experiment_dir, f"resource_manager.{self.compress_suffix}")
        self.file_system.dump_pickle(data_manager, data_manager_path)
        self.file_system.dump_pickle(copied_resource_manager, resource_manager_path)
        self.additional_info = additional_info
        experiment_record = self.ExperimentsModel.create(
            general_experiment_timestamp=general_experiment_timestamp,
            current_experiment_timestamp=current_experiment_timestamp,
            hdl_id=self.hdl_id,
            task_id=self.task_id,
            hdl_constructors=[str(item) for item in hdl_constructors],
            hdl_constructor=str(hdl_constructor),
            raw_hdl=raw_hdl,
            hdl=hdl,
            tuners=[str(item) for item in tuners],
            tuner=str(tuner),
            should_calc_all_metric=should_calc_all_metric,
            data_manager_path=data_manager_path,
            resource_manager_path=resource_manager_path,
            column_descriptions=column_descriptions,
            column2feature_groups=data_manager.column2feature_groups,  # todo
            dataset_metadata=dataset_metadata,
            metric=metric.name,
            splitter=str(splitter),
            ml_task=str(data_manager.ml_task),
            should_store_intermediate_result=should_store_intermediate_result,
            fit_ensemble_params=str(fit_ensemble_params),
            additional_info=additional_info,
            should_finally_fit=should_finally_fit
        )
        fetched_experiment_id = experiment_record.experiment_id
        if fetched_experiment_id != experiment_id:
            self.logger.warning("fetched_experiment_id != experiment_id")
        if set_id:
            self.experiment_id = experiment_id

    def init_experiments_table(self):
        if self.is_init_experiments_db:
            return
        self.is_init_experiments_db = True
        self.experiments_db: pw.Database = self.Datebase(**self.update_db_params(self.meta_records_db_name))
        self.ExperimentsModel = self.get_experiments_model()

    def close_experiments_table(self):
        self.is_init_experiments_db = False
        self.experiments_db = None
        self.ExperimentsModel = None

    # ----------tasks_model------------------------------------------------------------------
    def get_tasks_model(self) -> pw.Model:
        class Tasks(pw.Model):
            # task_id = md5(X_train, y_train, X_test, y_test, splitter, metric)
            task_id = pw.CharField(primary_key=True)
            metric = pw.CharField(default="")
            splitter = pw.CharField(default="")
            ml_task = pw.CharField(default="")
            specific_task_token = pw.CharField(default="")
            # Xy_train
            Xy_train_hash = pw.CharField(default="")
            Xy_train_path = pw.TextField(default="")
            # Xy_test
            Xy_test_hash = pw.CharField(default="")
            Xy_test_path = pw.TextField(default="")
            # meta info
            meta_data = self.JSONField(default={})

            class Meta:
                database = self.tasks_db

        self.tasks_db.create_tables([Tasks])
        return Tasks

    def insert_to_tasks_table(self, data_manager: DataManager,
                              metric: Scorer, splitter,
                              specific_task_token, dataset_metadata,
                              task_metadata, set_id=True):
        self.init_tasks_table()
        Xy_train_hash = get_hash_of_Xy(data_manager.X_train, data_manager.y_train)
        Xy_test_hash = get_hash_of_Xy(data_manager.X_test, data_manager.y_test)
        metric_str = metric.name
        splitter_str = str(splitter)
        ml_task_str = str(data_manager.ml_task)
        # ---task_id----------------------------------------------------
        m = hashlib.md5()
        get_hash_of_Xy(data_manager.X_train, data_manager.y_train, m)
        get_hash_of_Xy(data_manager.X_test, data_manager.y_test, m)
        get_hash_of_str(metric_str, m)
        get_hash_of_str(splitter_str, m)
        get_hash_of_str(ml_task_str, m)
        get_hash_of_str(specific_task_token, m)
        task_hash = m.hexdigest()
        task_id = task_hash
        records = self.TasksModel.select().where(self.TasksModel.task_id == task_id)
        meta_data = dict(
            dataset_metadata=dataset_metadata, **task_metadata
        )
        # ---store_task_record----------------------------------------------------
        if len(records) == 0:
            # ---store_datasets----------------------------------------------------
            Xy_train = [data_manager.X_train, data_manager.y_train]
            Xy_test = [data_manager.X_test, data_manager.y_test]
            Xy_train_path = self.file_system.join(self.datasets_dir,
                                                  f"{Xy_train_hash}.{self.compress_suffix}")
            self.file_system.dump_pickle(Xy_train, Xy_train_path)

            if Xy_test_hash:
                Xy_test_path = self.file_system.join(self.datasets_dir,
                                                     f"{Xy_test_hash}.{self.compress_suffix}")
                self.file_system.dump_pickle(Xy_test, Xy_test_path)
            else:
                Xy_test_path = ""

            self.TasksModel.create(
                task_id=task_id,
                metric=metric_str,
                splitter=splitter_str,
                ml_task=ml_task_str,
                specific_task_token=specific_task_token,
                # Xy_train
                Xy_train_hash=Xy_train_hash,
                Xy_train_path=Xy_train_path,
                # Xy_test
                Xy_test_hash=Xy_test_hash,
                Xy_test_path=Xy_test_path,
                meta_data=meta_data
            )
        else:
            record = records[0]
            old_meta_data = record.meta_data
            new_meta_data = update_data_structure(old_meta_data, meta_data)
            record.meta_data = new_meta_data
            record.save()
        if set_id:
            self.task_id = task_id

    def init_tasks_table(self):
        if self.is_init_tasks_db:
            return
        self.is_init_tasks_db = True
        self.tasks_db: pw.Database = self.Datebase(**self.update_db_params(self.meta_records_db_name))
        self.TasksModel = self.get_tasks_model()

    def close_tasks_table(self):
        self.is_init_tasks_db = False
        self.tasks_db = None
        self.TasksModel = None

    # ----------hdls_model------------------------------------------------------------------
    def get_hdls_model(self) -> pw.Model:
        class HDLs(pw.Model):
            hdl_id = pw.CharField(primary_key=True)
            hdl = self.JSONField(default={})
            meta_data = self.JSONField(default={})

            class Meta:
                database = self.hdls_db

        self.hdls_db.create_tables([HDLs])
        return HDLs

    def insert_to_hdls_table(self, hdl, hdl_metadata, set_id=True):
        self.init_hdls_table()
        hdl_hash = get_hash_of_dict(hdl)
        hdl_id = hdl_hash
        records = self.HDLsModel.select().where(self.HDLsModel.hdl_id == hdl_id)
        if len(records) == 0:
            self.HDLsModel.create(
                hdl_id=hdl_id,
                hdl=hdl,
                meta_data=hdl_metadata
            )
        else:
            record = records[0]
            old_meta_data = record.meta_data
            new_meta_data = update_data_structure(old_meta_data, hdl_metadata)
            record.meta_data = new_meta_data
            record.save()
        if set_id:
            self.hdl_id = hdl_id

    def init_hdls_table(self):
        if self.is_init_hdls_db:
            return
        self.is_init_hdls_db = True
        self.hdls_db: pw.Database = self.Datebase(**self.update_db_params(self.tasks_db_name))
        self.HDLsModel = self.get_hdls_model()

    def close_hdls_table(self):
        self.is_init_hdls_db = False
        self.hdls_db = None
        self.HDLsModel = None

    # ----------trials_model------------------------------------------------------------------

    def get_trials_model(self) -> pw.Model:
        class Trials(pw.Model):
            trial_id = pw.AutoField(primary_key=True)
            config_id = pw.CharField(default="")
            task_id = pw.CharField(default="")
            hdl_id = pw.CharField(default="")
            experiment_id = pw.IntegerField(default=0)
            estimator = pw.CharField(default="")
            loss = pw.FloatField(default=65535)
            losses = self.JSONField(default=[])
            test_loss = self.JSONField(default=[])  # 测试集
            all_score = self.JSONField(default={})
            all_scores = self.JSONField(default=[])
            test_all_score = self.JSONField(default={})  # 测试集
            models_path = pw.TextField(default="")
            final_model_path = pw.TextField(default="")
            # 都是将python对象进行序列化存储，是否合适？
            y_info_path = pw.TextField(default="")
            # ------------被附加的额外信息---------------
            additional_info = self.JSONField(default={})
            # -------------------------------------
            smac_hyper_param = PickleField(default=0)
            dict_hyper_param = self.JSONField(default={})
            cost_time = pw.FloatField(default=65535)
            status = pw.CharField(default="SUCCESS")
            failed_info = pw.TextField(default="")
            warning_info = pw.TextField(default="")
            intermediate_result_path = pw.TextField(default=""),
            timestamp = pw.DateTimeField(default=datetime.datetime.now)
            user = pw.CharField(default=getuser)
            pid = pw.IntegerField(default=os.getpid)

            class Meta:
                database = self.trials_db

        self.trials_db.create_tables([Trials])
        return Trials

    def init_trials_table(self):
        if self.is_init_trials_db:
            return
        self.is_init_trials_db = True
        self.trials_db: pw.Database = self.Datebase(**self.update_db_params(self.tasks_db_name))
        self.TrialsModel = self.get_trials_model()

    def close_trials_table(self):
        self.is_init_trials_db = False
        self.trials_db = None
        self.TrialsModel = None

    def insert_to_trials_table(self, info: Dict):
        self.init_trials_table()
        config_id = info.get("config_id")
        # 这个变量还是很有必要的，因为可能用户指定的切分器每次切的数据不一样

        models_path, intermediate_result_path, finally_fit_model_path, y_info_path = \
            self.persistent_evaluated_model(info, config_id)
        additional_info = deepcopy(self.additional_info)
        additional_info.update(info["additional_info"])
        self.TrialsModel.create(
            config_id=config_id,
            task_id=self.task_id,
            hdl_id=self.hdl_id,
            experiment_id=self.experiment_id,
            estimator=info.get("estimator", ""),
            loss=info.get("loss", 65535),
            losses=info.get("losses", []),
            test_loss=info.get("test_loss", 65535),
            all_score=info.get("all_score", {}),
            all_scores=info.get("all_scores", []),
            test_all_score=info.get("test_all_score", {}),
            models_path=models_path,
            final_model_path=finally_fit_model_path,
            y_info_path=y_info_path,
            additional_info=additional_info,
            smac_hyper_param=info.get("program_hyper_param"),
            dict_hyper_param=info.get("dict_hyper_param", {}),
            cost_time=info.get("cost_time", 65535),
            status=info.get("status", "failed"),
            failed_info=info.get("failed_info", ""),
            warning_info=info.get("warning_info", ""),
            intermediate_result_path=intermediate_result_path,
        )

    def delete_models(self):
        if hasattr(self, "sync_dict"):
            exit_processes = self.sync_dict.get("exit_processes", 3)
            records = 0
            for key, value in self.sync_dict.items():
                if isinstance(key, int):
                    records += value
            if records >= exit_processes:
                return False
        # master segment
        if not self.is_master:
            return True
        self.init_trials_table()
        # estimators = []
        # for record in self.TrialsModel.select(self.TrialsModel.trial_id, self.TrialsModel.estimator).group_by(
        #         self.TrialsModel.estimator):
        #     estimators.append(record.estimator)
        # for estimator in estimators:
        # .where(self.TrialsModel.estimator == estimator)
        should_delete = self.TrialsModel.select().order_by(
            self.TrialsModel.loss, self.TrialsModel.cost_time).offset(self.max_persistent_estimators)
        if len(should_delete):
            for record in should_delete:
                models_path = record.models_path
                self.logger.info(f"Delete expire Model in path : {models_path}")
                self.file_system.delete(models_path)
            self.TrialsModel.delete().where(
                self.TrialsModel.trial_id.in_(should_delete.select(self.TrialsModel.trial_id))).execute()
        return True


if __name__ == '__main__':
    rm = ResourceManager("/home/tqc/PycharmProjects/autoflow/test/test_db")
    rm.init_dataset_path("default_dataset_name")
    rm.init_trials_table()
    estimators = []
    for record in rm.TrialsModel.select().group_by(rm.TrialsModel.estimator):
        estimators.append(record.estimator)
    for estimator in estimators:
        should_delete = rm.TrialsModel.select(rm.TrialsModel.trial_id).where(
            rm.TrialsModel.estimator == estimator).order_by(
            rm.TrialsModel.loss, rm.TrialsModel.cost_time).offset(50)
        if should_delete:
            rm.TrialsModel.delete().where(rm.TrialsModel.trial_id.in_(should_delete)).execute()
