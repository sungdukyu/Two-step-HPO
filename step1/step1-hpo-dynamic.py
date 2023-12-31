import xarray as xr
import numpy as np
import pandas as pd
import tensorflow as tf
import os
from keras import models
from keras import layers
from keras import callbacks
import keras_tuner as kt

### USER parameters ###
projname = 'P05'
f_dataset = '~/data/p05_all_samples_output_cleaned_shuffled.nc'
vars_input  = ['tair', 'pressure', 'rh', 'wbar', 'num_aer', 'r_aer', 'kappa']
vars_output = ['fn']
validation_split = 0.2
compile_opt = {'optimizer': 'Adam',
               'loss': 'mse',
               'metrics': ['mae']
              }
search_opt = {'objective': "val_loss",
              'overwrite': False,
              'executions_per_trial': 1,
              'max_trials': 10000,
              'directory': './results'
             }
batch_size = 1024
max_epochs = 100 # Note that 'EarlyStoppoing' is enforced.
####

def set_environment(num_gpus_per_node="8"):
    nodename = os.environ['SLURMD_NODENAME']
    procid = os.environ['SLURM_LOCALID']
    print(nodename)
    print(procid)
    stream = os.popen('scontrol show hostname $SLURM_NODELIST')
    output = stream.read()
    oracle = output.split("\n")[0]
    print(oracle)
    if procid==num_gpus_per_node:
        os.environ["KERASTUNER_TUNER_ID"] = "chief"
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    else:
        os.environ["KERASTUNER_TUNER_ID"] = "tuner-" + str(nodename) + "-" + str(procid) 
        os.environ["CUDA_VISIBLE_DEVICES"] = procid

    os.environ["KERASTUNER_ORACLE_IP"] = oracle + ".ib.bridges2.psc.edu" # Use full hostname
    os.environ["KERASTUNER_ORACLE_PORT"] = "8000"
    print("KERASTUNER_TUNER_ID:    %s"%os.environ["KERASTUNER_TUNER_ID"])
    print("KERASTUNER_ORACLE_IP:   %s"%os.environ["KERASTUNER_ORACLE_IP"])
    print("KERASTUNER_ORACLE_PORT: %s"%os.environ["KERASTUNER_ORACLE_PORT"])
    #print(os.environ)

def vectorize(ds:xr.Dataset, v:list):
    for k, kvar in enumerate(v):
        if k == 0:
            train_input_vectorized = ds[kvar].to_pandas()
        else:
            train_input_vectorized = pd.concat([train_input_vectorized, ds[kvar].to_pandas()], axis=1)
            
    return train_input_vectorized.values

def main():
    #1 Load training dataset
    f_orig = xr.open_dataset(f_dataset)

    #2 Select variables relevant for emulator 
    f_train  = f_orig[vars_input]  # for input  -> gonna be normalized
    f_train0 = f_orig[vars_output] # for output -> not

    #3 Normalize
    mu    = f_train.mean('nsamples')
    sigma = f_train.std('nsamples')
    f_train = f_train - mu
    f_train = f_train / sigma

    #4 Vectorize input / output
    # (f_train is normalized, but f_train0 is not)
    train_input_vectorized  = vectorize(f_train,  vars_input)  # using f_train
    train_output_vectorized = vectorize(f_train0, vars_output) # using f_train0

    #5 divide train into train vs validation
    n_train = train_input_vectorized.shape[0]
    n_val   = int(n_train * validation_split)
    x_val   = train_input_vectorized[:n_val]
    x_train = train_input_vectorized[n_val:]
    y_val   = train_output_vectorized[:n_val]
    y_train = train_output_vectorized[n_val:]

    #6 hypermodel
    def build_model(hp):
        model = models.Sequential()

        for i in range(hp.Int("num_layers", 1, 20)):
            model.add(
                layers.Dense(
                    # Tune number of units separately.
                    units=hp.Choice(f"units_{i}", [8, 16, 32, 64, 128, 256, 512, 1024, 2048]),
                    activation="relu",
                )
            )

        model.add(layers.Dense(train_output_vectorized.shape[1], activation='sigmoid'))
        model.compile(**compile_opt)

        return model

    #7 search set up
    tuner = kt.RandomSearch(build_model,
                            project_name = projname,
                            **search_opt
                           )
    tuner.search_space_summary()

    #8 search
    tuner.search(x_train, y_train,
                 validation_data = (x_val, y_val),
                 batch_size = batch_size,
                 epochs = max_epochs,
                 verbose = 2,
                 callbacks = [callbacks.EarlyStopping('val_loss', patience=5)] 
                )

if __name__ == '__main__':
    # setting env variables for distributed search
    set_environment()

    # limit memory preallocation
    physical_devices = tf.config.list_physical_devices('GPU')
    print(physical_devices)
    tf.config.experimental.set_memory_growth(physical_devices[0], True) # only using a single GPU per trial

    # main HPO code
    main()
