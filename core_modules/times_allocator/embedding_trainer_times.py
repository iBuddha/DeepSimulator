# -*- coding: utf-8 -*-
"""
Created on Wed Nov 21 21:23:55 2018

@author: Manuel Camargo
"""
import itertools
import math
import os
import random

import numpy as np
import pandas as pd
import utils.support as sup

from core_modules.times_allocator.embedding_base import EmbeddingBase
from support_modules.common import FileExtensions as Fe
from keras.callbacks import ModelCheckpoint
from keras.layers import Input, Embedding, Dot, Reshape, Multiply
from keras.models import Model


class EmbeddingTrainer(EmbeddingBase):
    """
    This class evaluates the inter-arrival times
    """
    def load_embeddings(self):
        # Load embedded matrix
        if os.path.exists(os.path.join(self.embedded_path, self.embedding_file_name)):
            return self._read_embedded(self.index_ac, self.embedding_file_name)
        else:
            usr_idx = lambda x: self.usr_index.get(x['user'])
            self.log['usr_index'] = self.log.apply(usr_idx, axis=1)

            self.log['duration'] = self.log.apply(lambda x: (x['end_timestamp'] - x['start_timestamp']).total_seconds(),
                                                  axis=1)
            n_bins = int(
                (np.max(self.log['duration']) - np.min(self.log['duration'])) / (np.mean(self.log['duration'])))
            print('The number of intervals are: {}'.format(n_bins))
            self.log['time'] = pd.qcut(self.log['duration'], n_bins, labels=False).astype(str)

            self.time_index = {x: idx for idx, x in enumerate(self.log['time'].drop_duplicates())}
            self.index_time = {idx: x for idx, x in enumerate(self.log['time'].drop_duplicates())}

            time_idx = lambda x: self.time_index.get(x['time'])
            self.log['time_index'] = self.log.apply(time_idx, axis=1)

            dim_number = math.ceil(
                len(list(itertools.product(*[list(self.ac_index.items()),
                                             list(self.usr_index.items())]))) ** 0.25)
            self._train_embedded(dim_number)

            if not os.path.exists(self.embedded_path):
                os.makedirs(self.embedded_path)

            matrix = self._reformat_matrix(self.index_ac, self.ac_weights)
            sup.create_file_from_list(matrix, os.path.join(self.embedded_path, self.embedding_file_name))
            return self.ac_weights

    # =============================================================================
    # Pre-processing: embedded dimension
    # =============================================================================

    def _train_embedded(self, dim_number):
        """Carry out the training of the embeddings"""
        # Iterate through each book
        model = self._create_model(dim_number)
        model.summary()

        vec, cl = self._vectorize_input(self.log, negative_ratio=2)

        # Output file
        output_file_path = os.path.join(self.embedded_path, self.embedding_model_file_name)
        # Saving
        model_checkpoint = ModelCheckpoint(output_file_path,
                                           monitor='val_loss',
                                           verbose=0,
                                           save_best_only=True,
                                           save_weights_only=False,
                                           mode='auto')
        # Train
        print(vec)
        model.fit(x=vec, y=cl,
                  validation_split=0.2,
                  callbacks=[model_checkpoint],
                  epochs=100,
                  verbose=2)

        # Extract embeddings
        ac_layer = model.get_layer('activity_embedding')
        self.ac_weights = ac_layer.get_weights()[0]

    def _vectorize_input(self, log, negative_ratio=1.0):
        pairs = list()
        for i in range(0, len(self.log)):
            # Iterate through the links in the book
            pairs.append((self.ac_index[self.log.iloc[i]['task']],
                          self.usr_index[self.log.iloc[i]['user']],
                          self.time_index[self.log.iloc[i]['time']]))

        n_positive = math.ceil(len(self.log) / 2)
        batch_size = n_positive * (1 + negative_ratio)
        batch = np.zeros((batch_size, 4))
        pairs_set = set(pairs)
        activities = list(self.ac_index.keys())
        users = list(self.usr_index.keys())
        times = list(self.time_index.keys())
        # This creates a generator
        # randomly choose positive examples
        idx = 0
        for idx, (activity, user, time) in enumerate(random.sample(pairs,
                                                                   n_positive)):
            batch[idx, :] = (activity, user, time, 1)
        # Increment idx by 1
        idx += 1

        # Add negative examples until reach batch size
        while idx < batch_size:
            # random selection
            random_ac = random.randrange(len(activities) - 1)
            random_rl = random.randrange(len(users) - 1)
            random_tm = random.randrange(len(times) - 1)

            # Check to make sure this is not a positive example
            if (random_ac, random_rl, random_tm) not in pairs_set:
                # Add to batch and increment index,  0 due classification task
                batch[idx, :] = (random_ac, random_rl, random_tm, 0)
                idx += 1

        # Make sure to shuffle order
        np.random.shuffle(batch)
        return {'activity': batch[:, 0], 'user': batch[:, 1], 'time': batch[:, 2]}, batch[:, 3]
        #     yield 

    def _create_model(self, embedding_size):
        """Model to embed activities and users using the functional API"""

        # Both inputs are 1-dimensional
        activity = Input(name='activity', shape=[1])
        user = Input(name='user', shape=[1])
        time = Input(name='time', shape=[1])

        # Poner matriz de weights en la entrada con la salida de los weighting embeddings
        # Embedding the activity (shape will be (None, 1, embedding_size))
        activity_embedding = Embedding(name='activity_embedding',
                                       input_dim=len(self.ac_index),
                                       output_dim=embedding_size)(activity)

        # Embedding the user (shape will be (None, 1, embedding_size))
        user_embedding = Embedding(name='user_embedding',
                                   input_dim=len(self.usr_index),
                                   output_dim=embedding_size)(user)

        # Embedding the user (shape will be (None, 1, embedding_size))
        time_embedding = Embedding(name='time_embedding',
                                   input_dim=len(self.time_index),
                                   output_dim=embedding_size)(time)

        # Merge the layers with a dot product
        # along the second axis (shape will be (None, 1, 1))
        # merged = Multiply(name='dot_product')([activity_embedding, user_embedding, time_embedding])

        merged_act_usr = Dot(name='dot_product_act_usr',
                             normalize=True, axes=2)([activity_embedding, user_embedding])

        merged_usr_tim = Dot(name='dot_product_usr_tim',
                             normalize=True, axes=2)([user_embedding, time_embedding])

        merged_act_tim = Dot(name='dot_product_act_tim',
                             normalize=True, axes=2)([activity_embedding, time_embedding])

        merged = Multiply(name='merge_mul')([merged_act_usr, merged_usr_tim, merged_act_tim])

        # Reshape to be a single number (shape will be (None, 1))
        merged = Reshape(target_shape=[1])(merged)

        # Loss function is mean squared error
        model = Model(inputs=[activity, user, time], outputs=merged)
        model.compile(optimizer='Adam', loss='mse')

        return model

    # =============================================================================
    # Support
    # =============================================================================

    @staticmethod
    def _reformat_matrix(index, weigths):
        """Reformating of the embedded matrix for exporting.
        Args:
            index: index of activities or users.
            weigths: matrix of calculated coordinates.
        Returns:
            matrix with indexes.
        """
        matrix = list()
        for i, _ in enumerate(index):
            data = [i, index[i]]
            data.extend(weigths[i])
            matrix.append(data)
        return matrix

    def _read_embedded(self, index, filename):
        """Loading of the embedded matrices.
        params:
            index (dict): index of activities or roles.
            filename (str): filename of the matrix file.
        Returns:
            numpy array: array of weights.
        """
        weights = pd.read_csv(os.path.join(self.embedded_path, filename), header=None)
        weights[1] = weights.apply(lambda x: x[1].strip(), axis=1)
        if set(list(index.values())) == set(weights[1].tolist()):
            weights = weights.drop(columns=[0, 1])
            return np.array(weights)
        else:
            raise KeyError('Inconsistency in the number of activities')
