from model.methods.base import Method
import torch
import torch
import torch.nn.functional as F
import numpy as np

from model.lib.data import (
    Dataset,
    data_nan_process,
    data_enc_process,
    data_norm_process,
    data_label_process
)

class TabCapsMethod(Method):
    def __init__(self, args, is_regression):
        super().__init__(args, is_regression)
        assert(args.cat_policy != 'indices')
        assert(is_regression == False)

    def construct_model(self, model_config = None):
        from model.models.tabcaps import TabCapsClassifier
        from qhoptim.pyt import QHAdam
        if model_config is None:
            model_config = self.args.config['model']
        self.model = TabCapsClassifier(
            optimizer_fn=QHAdam,
            optimizer_params=dict(lr=model_config['lr'], weight_decay=model_config['weight_decay'], nus=(0.7, 0.99), betas=(0.95, 0.998)),
            scheduler_params=dict(gamma=0.95, step_size=20),
            scheduler_fn=torch.optim.lr_scheduler.StepLR,
            sub_class=model_config['sub_class'],
            init_dim=model_config['init_dim'],
            primary_capsule_size=model_config['primary_capsule_size'],
            digit_capsule_size=model_config['digit_capsule_size'],
            leaves=model_config['leaves'],
            seed=self.args.seed
        )
        
    def data_format(self, is_train = True, N = None, C = None, y = None):
        self.criterion = F.cross_entropy 
        if is_train:
            self.N, self.C, self.num_new_value, self.imputer, self.cat_new_value = data_nan_process(self.N, self.C, self.args.num_nan_policy, self.args.cat_nan_policy)
            self.y, self.y_info, self.label_encoder = data_label_process(self.y, self.is_regression)
            self.N, self.C, self.ord_encoder, self.mode_values, self.cat_encoder = data_enc_process(self.N, self.C, self.args.cat_policy)
            self.N, self.normalizer = data_norm_process(self.N, self.args.normalization, self.args.seed)
            if self.is_regression:
                self.d_out = 1
            else:
                self.d_out = len(np.unique(self.y['train']))
            self.d_in = 0 if self.N is None else self.N['train'].shape[1]
        else:
            N_test, C_test, _, _, _ = data_nan_process(N, C, self.args.num_nan_policy, self.args.cat_nan_policy, self.num_new_value, self.imputer, self.cat_new_value)
            y_test, _, _ = data_label_process(y, self.is_regression, self.y_info, self.label_encoder)
            N_test, C_test, _, _, _ = data_enc_process(N_test, C_test, self.args.cat_policy, None, self.ord_encoder, self.mode_values, self.cat_encoder)
            N_test, _ = data_norm_process(N_test, self.args.normalization, self.args.seed, self.normalizer)
            self.N_test = N_test['test']
            self.y_test = y_test['test']
      
    def fit(self, N, C, y, info, train = True, config = None):
        if self.D is None:
            self.D = Dataset(N, C, y, info)
            self.N, self.C, self.y = self.D.N, self.D.C, self.D.y
            self.is_binclass, self.is_multiclass, self.is_regression = self.D.is_binclass, self.D.is_multiclass, self.D.is_regression
            self.n_num_features, self.n_cat_features = self.D.n_num_features, self.D.n_cat_features
            self.data_format(is_train = True)
        if config is not None:
            self.reset_stats_withconfig(config)
        self.construct_model()
        # if not train, skip the training process. such as load the checkpoint and directly predict the results
        if not train:
            return
        X_train = self.N['train']
        y_train = self.y['train']
        X_valid = self.N['val']
        y_valid = self.y['val']
        eval_metric = ['accuracy']
        result, loss, auc = self.model.fit(
            X_train=X_train, y_train=y_train,
            eval_set=[(X_valid, y_valid)],
            eval_name=['valid'],
            eval_metric=eval_metric,
            max_epochs=self.args.max_epoch, patience=20,
            batch_size=self.args.batch_size, virtual_batch_size=256,
            device_id=self.args.gpu
        )
        self.model.save_check(self.args.save_path, self.args.seed)
        self.trlog['best_res'] = self.model.best_cost
        return

    def predict(self, N, C, y, info, model_name):
        self.data_format(False, N, C, y)
        test_label, test_logit, _ = self.model.predict(self.N_test, self.y_test)
        vl = self.criterion(torch.tensor(test_logit), torch.tensor(test_label)).item()     
        vres, metric_name = self.metric(test_logit, test_label, self.y_info)
        print('Test: loss={:.4f}'.format(vl))
        for name, res in zip(metric_name, vres):
            print('[{}]={:.4f}'.format(name, res))

        return vl, vres, metric_name, test_logit