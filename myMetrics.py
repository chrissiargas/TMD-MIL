import io
import numpy as np
import pandas as pd
import tensorflow as tf
import keras
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
import matplotlib.pyplot as plt
import seaborn as sns
from keras.callbacks import Callback
import time
import os


def plot_to_image(figure):
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(figure)
    buf.seek(0)

    image = tf.image.decode_png(buf.getvalue(), channels=4)
    image = tf.expand_dims(image,0)

    return image


class valMetrics(Callback):
    def __init__(self, val, batchSize, steps, verbose = 1):
        super(valMetrics, self).__init__()
        self.val = val
        self.batchSize = batchSize
        self.steps = steps
        self.score = 'macro'
        self.verbose = verbose

    def on_epoch_end(self, epoch, logs={}):
        total = self.batchSize * self.steps
        step = 0
        val_predict = np.zeros(total)
        val_true = np.zeros(total)

        for batch in self.val.take(self.steps):

            val_data = batch[0]
            val_target = batch[1]

            val_predict[step * self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(self.model.predict(val_data, verbose=0)),axis=1)
            val_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(val_target,axis=1)
            step += 1

        f1 = f1_score(val_true, val_predict, average=self.score)
        recall = recall_score(val_true, val_predict, average=self.score)
        precision = precision_score(val_true, val_predict, average=self.score)

        del val_predict
        del val_true

        if self.verbose:
            print(" - val f1: %f - val precision: %f - val recall: %f" %(f1,precision,recall))

        return


class testMetrics(Callback):
    def __init__(self, test, batchSize, steps, verbose = 1):
        super(testMetrics, self).__init__()
        self.test = test
        self.batchSize = batchSize
        self.steps = steps
        self.score = 'macro'
        self.verbose = verbose

    def on_test_end(self, logs={}):
        total = self.batchSize * self.steps
        step = 0
        test_predict = np.zeros(total)
        test_true = np.zeros(total)

        for batch in self.test.take(self.steps):

            test_data = batch[0]
            test_target = batch[1]

            test_predict[step*self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(self.model.predict(test_data, verbose=0)), axis=1)
            test_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(test_target,axis=1)
            step += 1

        test_f1 = f1_score(test_true, test_predict, average=self.score)
        test_recall = recall_score(test_true, test_predict, average=self.score)
        test_precision = precision_score(test_true, test_predict, average=self.score)

        del test_predict
        del test_true

        if self.verbose:
            print(" - test f1: %f - test precision: %f - test recall %f" %(test_f1,test_precision,test_recall))

        return


class valTables(Callback):
    def __init__(self,
                 args,
                 val,
                 batchSize,
                 steps,
                 file_writer,
                 weights_file_writer,
                 weights_pos_file_writer):
        super(valTables, self).__init__()

        self.val = val
        self.batchSize = batchSize
        self.steps = steps
        self.accBagSize = args.train_args['accBagSize']
        self.gpsBagSize = 1
        self.accMIL = args.train_args['separate_MIL']
        self.n_heads = args.train_args['heads']

        self.motorized = args.train_args['motorized']
        self.n_classes = 5 if self.motorized else 8

        if self.motorized:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Motorized'
            ]

        else:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Car',
                'Bus',
                'Train',
                'Subway'
            ]

        self.file_writer = file_writer
        self.weights_file_writer = weights_file_writer
        self.weights_pos_file_writer = weights_pos_file_writer
        self.bagPositions = args.train_args['train_bag_positions']
        self.random_position = self.bagPositions != 'same'
        self.posPerInstance = 4 if self.bagPositions == 'all' else 1
        self.accBagSize *= self.posPerInstance

        if args.data_args['dataset'] == 'CompleteUser1':
            self.pnl = ['Hips']

        else:
            self.pnl = ['Torso','Hips','Bag','Hand']

    def on_train_end(self, logs=None):
        total = self.batchSize * self.steps
        step = 0
        val_predict = np.zeros(total)
        val_true = np.zeros(total)

        weights = []
        weighting = []
        acc_weights = []
        acc_weighting = None
        positional = None
        positions = None
        wm_pos_count = None
        wm_pos_sum = None

        if self.accMIL:
            for head in range(self.n_heads):
                weighting.append(keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("weight_layer_" + str(head)).output
                ))

                weights.append(np.zeros((total, 2)))

            if self.random_position:
                acc_weighting = keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer('acc_weight_layer').output
                )

                positional = keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("positional").output
                )

                acc_weights = np.zeros((total, self.accBagSize))
                positions = np.zeros((total, self.accBagSize))

        else:
            for head in range(self.n_heads):
                weighting.append(keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("weight_layer_" + str(head)).output
                ))

                weights.append(np.zeros((total, self.gpsBagSize + self.accBagSize)))

            if self.random_position:

                positional = keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("positional").output
                )

                positions = np.zeros((total, self.accBagSize))

                for head in range(self.n_heads):
                    acc_weights.append(np.zeros((total, self.accBagSize)))

        for batch in self.val.take(self.steps):

            val_data = batch[0]
            val_target = batch[1]

            pred = self.model.call(val_data)
            val_predict[step*self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(pred),axis=1)
            val_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(val_target,axis=1)

            for head in range(self.n_heads):
                weights[head][step * self.batchSize: (step + 1) * self.batchSize] = weighting[head](val_data)

            if self.random_position:
                if self.accMIL:
                    acc_weights[step * self.batchSize: (step + 1) * self.batchSize] = acc_weighting(val_data)
                    positions[step * self.batchSize: (step + 1) * self.batchSize] = positional(val_data)

                else:
                    for head in range(self.n_heads):
                        acc_weights[head][step * self.batchSize: (step + 1) * self.batchSize] = weighting[head](val_data)[:, :self.accBagSize]
                    positions[step * self.batchSize: (step + 1) * self.batchSize] = positional(val_data)

            step += 1

        val_f1 = f1_score(val_true, val_predict, average="macro")
        val_recall = recall_score(val_true, val_predict, average="macro")
        val_precision = precision_score(val_true, val_predict, average="macro")

        print(" - val f1: %f - val precision: %f - val recall: %f" %(val_f1,val_precision,val_recall))

        cm = confusion_matrix(val_true,val_predict)
        global CM
        CM = cm / cm.sum(axis=1)[:, np.newaxis]
        cm_df = pd.DataFrame(cm,
                             index = self.class_names,
                             columns = self.class_names)
        cm_df = cm_df.astype('float') / cm.sum(axis=1)[:, np.newaxis]

        figure = plt.figure(figsize=(10,10))
        sns.heatmap(cm_df, annot = True)
        plt.title('Confusion Matrix')
        plt.ylabel('Actual Values')
        plt.xlabel('Predicted Values')
        cm_image = plot_to_image(figure)

        with self.file_writer.as_default():
            tf.summary.image('Confusion Matrix', cm_image, step=1)

        fig, axs = plt.subplots(ncols=self.n_heads, figsize=(12, 16))
        fig.subplots_adjust(wspace=0.01)
        fig.suptitle('Weight Matrix')

        for head in range(self.n_heads):
            instances = ["Acceleration " + str(i + 1) for i in range(self.accBagSize)]
            wm_pred = np.concatenate([val_predict[:, np.newaxis], weights[head]], axis=1)

            if self.accMIL:
                wm_pred_df = pd.DataFrame(
                    wm_pred,
                    columns=['class', 'Acceleration', 'Location']
                )

            else:
                wm_pred_df = pd.DataFrame(
                    wm_pred,
                    columns=['class', *instances, 'GPS' ]
                )

                wm_pred_df['Accelerometer'] = wm_pred_df.loc[:, instances].sum(axis=1)
                wm_pred_df.drop(instances, inplace=True, axis=1)

            wm = wm_pred_df.groupby(['class'], as_index=False).mean()
            del wm['class']

            if self.n_heads == 1:

                sns.heatmap(wm, ax=axs, cbar=False, annot=True)
                if self.accMIL:
                    axs.set_xticklabels(labels=['Accelerometer', 'GPS'])
                else:
                    axs.set_xticklabels(labels=['GPS', 'Accelerometer'])
                axs.set_yticklabels(labels=self.class_names,
                                    rotation=45)

                fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)

            else:
                sns.heatmap(wm, ax=axs[head], cbar=False, annot=True)

                if self.accMIL:
                    axs[head].set_xticklabels(labels=['Accelerometer', 'GPS'])
                else:
                    axs[head].set_xticklabels(labels=['GPS', 'Accelerometer'])

                if head == 0:
                    axs[head].set_yticklabels(labels=self.class_names,
                                              rotation=45)

                if head == self.n_heads - 1:
                    fig.colorbar(axs[head].collections[0], ax=axs[head], location="right", use_gridspec=False,
                                 pad=0.2)

        wm_image = plot_to_image(fig)

        with self.weights_file_writer.as_default():
            tf.summary.image('Weight Matrix', wm_image, step=1)

        if self.random_position:

            if not self.accMIL:
                fig, axs = plt.subplots(ncols=self.n_heads, figsize=(12, 16))
                fig.subplots_adjust(wspace=0.01)
                fig.suptitle('Weight Matrix')

                for head in range(self.n_heads):
                    if self.n_heads == 1:

                        for i in range(self.accBagSize):
                            wm_pred = np.concatenate(
                                [val_predict[:, np.newaxis], positions[:, [i]], acc_weights[head][:, [i]]], axis=1)
                            wm_pred_df = pd.DataFrame(
                                wm_pred,
                                columns=['class', 'position', 'weight']
                            )

                            if i == 0:
                                wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"],
                                                            columns=["position"],
                                                            fill_value=0)

                                wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"],
                                                              columns=["position"],
                                                              fill_value=0)

                            else:
                                wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = wm_pos_sum.add(
                                    pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                   fill_value=0).values)

                                wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = wm_pos_count.add(
                                    pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                   columns=["position"],
                                                   fill_value=0).values)

                            if i == self.accBagSize - 1:
                                wm_pos = wm_pos_sum.div(wm_pos_count.values)
                                wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                                sns.heatmap(wm_pos, ax=axs, cbar=False, annot=True)
                                fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)
                                axs.set_yticklabels(labels=self.class_names, rotation=45)
                                axs.set_xticklabels(labels=self.pnl)

                    else:

                        for i in range(self.accBagSize):
                            wm_pred = np.concatenate(
                                [val_predict[:, np.newaxis], positions[:, [i]], acc_weights[head][:, [i]]], axis=1)
                            wm_pred_df = pd.DataFrame(
                                wm_pred,
                                columns=['class', 'position', 'weight']
                            )

                            if i == 0:
                                wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"],
                                                            columns=["position"],
                                                            fill_value=0)

                                wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"],
                                                              columns=["position"],
                                                              fill_value=0)

                            else:
                                wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = wm_pos_sum.add(
                                    pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                   fill_value=0).values)

                                wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = wm_pos_count.add(
                                    pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                   columns=["position"],
                                                   fill_value=0).values)

                            if i == self.accBagSize - 1:
                                wm_pos = wm_pos_sum.div(wm_pos_count.values)
                                wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                                sns.heatmap(wm_pos, ax=axs[head], cbar=False, annot=True)


                                axs[head].set_xticklabels(labels=self.pnl)

                                if head == 0:
                                    axs[head].set_yticklabels(labels=self.class_names, rotation=45)

                                if head == self.n_heads - 1:
                                    fig.colorbar(axs[head].collections[0], ax=axs[head], location="right", use_gridspec=False,
                                                 pad=0.2)

            else:
                fig, axs = plt.subplots(ncols=1, figsize=(12, 16))
                fig.suptitle('Weight Matrix')

                for i in range(self.accBagSize):
                    wm_pred = np.concatenate([val_predict[:, np.newaxis], positions[:, [i]], acc_weights[:, [i]]], axis=1)
                    wm_pred_df = pd.DataFrame(
                        wm_pred,
                        columns=['class', 'position', 'weight']
                    )

                    if i==0:
                        wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                        wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"], columns=["position"],
                                                    fill_value=0)

                        wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                        wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"], columns=["position"],
                                                        fill_value=0)

                    else:
                        wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                        wm_pos_sum = wm_pos_sum.add(pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                    fill_value=0).values)

                        wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                        wm_pos_count = wm_pos_count.add(pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                      columns=["position"],
                                                      fill_value=0).values)

                    if i == self.accBagSize-1:
                        wm_pos = wm_pos_sum.div(wm_pos_count.values)
                        wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                        sns.heatmap(wm_pos, ax=axs, cbar=False, annot=True)
                        fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)
                        axs.set_yticklabels(labels=self.class_names, rotation=45)
                        axs.set_xticklabels(labels=self.pnl)

            wm_image = plot_to_image(fig)

            with self.weights_pos_file_writer.as_default():
                tf.summary.image('Position-Weight Matrix', wm_image, step=1)

        return


class testTables(Callback):
    def __init__(self,
                 args,
                 test,
                 batchSize,
                 steps,
                 file_writer,
                 weights_file_writer,
                 weights_pos_file_writer):

        super(testTables, self).__init__()
        self.test = test
        self.batchSize = batchSize
        self.steps = steps
        self.accBagSize = args.train_args['accBagSize']
        self.locBagSize = 1
        self.accMIL = args.train_args['separate_MIL']
        self.n_heads = args.train_args['heads']

        self.motorized = args.train_args['motorized']
        self.n_classes = 5 if self.motorized else 8

        if self.motorized:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Motorized'
            ]

        else:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Car',
                'Bus',
                'Train',
                'Subway'
            ]

        self.file_writer = file_writer
        self.weights_file_writer = weights_file_writer
        self.weights_pos_file_writer = weights_pos_file_writer
        self.bagPositions = args.train_args['test_bag_positions']
        self.random_position = self.bagPositions != 'same'
        self.posPerInstance = 4 if self.bagPositions == 'all' else 1
        self.accBagSize *= self.posPerInstance
        self.user = args.train_args['test_user']

        if args.data_args['dataset'] == 'CompleteUser1':
            self.pnl = ['Hips']

        else:
            self.pnl = ['Torso','Hips','Bag','Hand']

        self.path = os.path.join("saves", "Weights-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.makedirs(self.path)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

    def on_test_end(self, logs=None):
        total = self.batchSize * self.steps
        step = 0
        test_predict = np.zeros(total)
        test_true = np.zeros(total)

        weights = []
        weighting = []
        acc_weights = []
        acc_weighting = None
        positional = None
        positions = None
        wm_pos_sum = None
        wm_pos_count = None

        if self.accMIL:
            for head in range(self.n_heads):
                weighting.append(keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("weight_layer_" + str(head)).output
                ))

                weights.append(np.zeros((total, 2)))

            if self.random_position:
                acc_weighting = keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer('acc_weight_layer').output
                )

                positional = keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("positional").output
                )

                acc_weights = np.zeros((total, self.accBagSize))
                positions = np.zeros((total, self.accBagSize))

        else:
            for head in range(self.n_heads):
                weighting.append(keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("weight_layer_" + str(head)).output
                ))

                weights.append(np.zeros((total, self.locBagSize + self.accBagSize)))

            if self.random_position:

                positional = keras.models.Model(
                    inputs=self.model.input,
                    outputs=self.model.get_layer("positional").output
                )

                positions = np.zeros((total, self.accBagSize))

                for head in range(self.n_heads):
                    acc_weights.append(np.zeros((total, self.accBagSize)))

        for batch in self.test.take(self.steps):
            test_data = batch[0]
            test_target = batch[1]
            pred = self.model.call(test_data)

            test_predict[step * self.batchSize: (step + 1) * self.batchSize] = \
                np.argmax(np.asarray(pred), axis=1)
            test_true[step * self.batchSize: (step + 1) * self.batchSize] = np.argmax(test_target, axis=1)

            for head in range(self.n_heads):
                weights[head][step * self.batchSize: (step + 1) * self.batchSize] = weighting[head](test_data)

            if self.random_position:
                if self.accMIL:
                    acc_weights[step * self.batchSize: (step + 1) * self.batchSize] = acc_weighting(test_data)
                    positions[step * self.batchSize: (step + 1) * self.batchSize] = positional(test_data)

                else:
                    for head in range(self.n_heads):
                        acc_weights[head][step * self.batchSize: (step + 1) * self.batchSize] =  weighting[head](test_data)[:, :self.accBagSize]
                    positions[step * self.batchSize: (step + 1) * self.batchSize] = positional(test_data)

            step += 1

        test_f1 = f1_score(test_true, test_predict, average="macro")
        test_recall = recall_score(test_true, test_predict, average="macro")
        test_precision = precision_score(test_true, test_predict, average="macro")

        print(" - test f1: %f - test precision: %f - test recall %f" % (test_f1,test_precision,test_recall))

        cm = confusion_matrix(test_true, test_predict)
        cm_df = pd.DataFrame(cm,
                             index=self.class_names,
                             columns=self.class_names)
        cm_df = cm_df.astype('float') / cm.sum(axis=1)[:,np.newaxis]

        figure = plt.figure(figsize=(10, 10))
        sns.heatmap(cm_df, annot=True)
        plt.title('Confusion Matrix')
        plt.ylabel('Actual Values')
        plt.xlabel('Predicted Values')
        cm_image = plot_to_image(figure)

        with self.file_writer.as_default():
            tf.summary.image('Confusion Matrix', cm_image, step=1)

        fig, axs = plt.subplots(ncols=self.n_heads, figsize=(12, 16))
        fig.subplots_adjust(wspace=0.01)
        fig.suptitle('Weight Matrix')

        for head in range(self.n_heads):
            modes = test_predict[:, np.newaxis]
            ws_acc = np.concatenate([test_predict[:, np.newaxis], weights[head][:, :-1]], axis=1)

            ws_std = ws_acc.std(axis=1)[:, np.newaxis]
            std_mode = np.concatenate([modes, ws_std],  axis=1)
            std_df = pd.DataFrame(
                std_mode,
                columns=['class', 'std']
            )
            std_df = std_df.groupby(['class'], as_index=False).mean()
            print(std_df)

            wmStdFile = os.path.join(self.path, "WeightStd-" + str(self.user) + ".csv")
            std_df.to_csv(wmStdFile, index=False)

            instances = ["Acceleration " + str(i + 1) for i in range(self.accBagSize)]
            wm_pred = np.concatenate([test_predict[:, np.newaxis], weights[head]], axis=1)

            if self.accMIL:
                wm_pred_df = pd.DataFrame(
                    wm_pred,
                    columns=['class', 'Acceleration', 'Location']
                )

            else:
                wm_pred_df = pd.DataFrame(
                    wm_pred,
                    columns=['class', *instances, 'GPS']
                )

                wm_pred_df['Accelerometer'] = wm_pred_df.loc[:, instances].sum(axis=1)
                wm_pred_df.drop(instances, inplace=True, axis=1)

            wm = wm_pred_df.groupby(['class'], as_index=False).mean()
            print(wm)

            wmModFile = os.path.join(self.path, "WeightModality-" + str(self.user) + ".csv")
            wm.to_csv(wmModFile, index=False)

            del wm['class']

            if self.n_heads == 1:

                sns.heatmap(wm, ax=axs, cbar=False, annot=True)

                if self.accMIL:
                    axs.set_xticklabels(labels=['Accelerometer', 'GPS'])
                else:
                    axs.set_xticklabels(labels=['GPS', 'Accelerometer'])

                axs.set_yticklabels(labels=self.class_names,
                                    rotation=45)

                fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)

            else:

                sns.heatmap(wm, ax=axs[head], cbar=False, annot=True)

                if self.accMIL:
                    axs[head].set_xticklabels(labels=['Accelerometer', 'GPS'])
                else:
                    axs[head].set_xticklabels(labels=['GPS', 'Accelerometer'])

                if head == 0:
                    axs[head].set_yticklabels(labels=self.class_names,
                                              rotation=45)

                if head == self.n_heads - 1:
                    fig.colorbar(axs[head].collections[0], ax=axs[head], location="right", use_gridspec=False,
                                 pad=0.2)

        wm_image = plot_to_image(fig)

        with self.weights_file_writer.as_default():
            tf.summary.image('Weight Matrix', wm_image, step=1)

        if self.random_position:

            if not self.accMIL:
                fig, axs = plt.subplots(ncols=self.n_heads, figsize=(12, 16))
                fig.subplots_adjust(wspace=0.01)
                fig.suptitle('Weight Matrix')

                for head in range(self.n_heads):
                    if self.n_heads == 1:

                        for i in range(self.accBagSize):
                            wm_pred = np.concatenate(
                                [test_predict[:, np.newaxis], positions[:, [i]], acc_weights[head][:, [i]]], axis=1)
                            wm_pred_df = pd.DataFrame(
                                wm_pred,
                                columns=['class', 'position', 'weight']
                            )

                            if i == 0:
                                wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"],
                                                            columns=["position"],
                                                            fill_value=0)

                                wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"],
                                                              columns=["position"],
                                                              fill_value=0)

                            else:
                                wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = wm_pos_sum.add(
                                    pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                   fill_value=0).values)

                                wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = wm_pos_count.add(
                                    pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                   columns=["position"],
                                                   fill_value=0).values)

                            if i == self.accBagSize - 1:
                                wm_pos = wm_pos_sum.div(wm_pos_count.values)
                                wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                                sns.heatmap(wm_pos, ax=axs, cbar=False, annot=True)
                                fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)
                                axs.set_yticklabels(labels=self.class_names, rotation=45)
                                axs.set_xticklabels(labels=self.pnl)

                    else:

                        for i in range(self.accBagSize):
                            wm_pred = np.concatenate(
                                [test_predict[:, np.newaxis], positions[:, [i]], acc_weights[head][:, [i]]], axis=1)
                            wm_pred_df = pd.DataFrame(
                                wm_pred,
                                columns=['class', 'position', 'weight']
                            )

                            if i == 0:
                                wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"],
                                                            columns=["position"],
                                                            fill_value=0)

                                wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"],
                                                              columns=["position"],
                                                              fill_value=0)

                            else:
                                wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                                wm_pos_sum = wm_pos_sum.add(
                                    pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                   fill_value=0).values)

                                wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(
                                    name='size').reset_index()
                                wm_pos_count = wm_pos_count.add(
                                    pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                   columns=["position"],
                                                   fill_value=0).values)

                            if i == self.accBagSize - 1:
                                wm_pos = wm_pos_sum.div(wm_pos_count.values)
                                wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                                sns.heatmap(wm_pos, ax=axs[head], cbar=False, annot=True)

                                axs[head].set_xticklabels(labels=self.pnl)

                                if head == 0:
                                    axs[head].set_yticklabels(labels=self.class_names, rotation=45)

                                if head == self.n_heads - 1:
                                    fig.colorbar(axs[head].collections[0], ax=axs[head], location="right", use_gridspec=False,
                                                 pad=0.2)

            else:
                fig, axs = plt.subplots(ncols=1, figsize=(12, 16))
                fig.suptitle('Weight Matrix')

                for i in range(self.accBagSize):
                    wm_pred = np.concatenate([test_predict[:, np.newaxis], positions[:, [i]], acc_weights[:, [i]]], axis=1)
                    wm_pred_df = pd.DataFrame(
                        wm_pred,
                        columns=['class', 'position', 'weight']
                    )

                    if i==0:
                        wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                        wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"], columns=["position"],
                                                    fill_value=0)

                        wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                        wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"], columns=["position"],
                                                        fill_value=0)

                    else:
                        wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                        wm_pos_sum = wm_pos_sum.add(pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                    fill_value=0).values)

                        wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                        wm_pos_count = wm_pos_count.add(pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                      columns=["position"],
                                                      fill_value=0).values)

                    if i == self.accBagSize-1:
                        wm_pos = wm_pos_sum.div(wm_pos_count.values)
                        wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                        sns.heatmap(wm_pos, ax=axs, cbar=False, annot=True)
                        fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)
                        axs.set_yticklabels(labels=self.class_names, rotation=45)
                        axs.set_xticklabels(labels=self.pnl)

            wm_image = plot_to_image(fig)

            with self.weights_pos_file_writer.as_default():
                tf.summary.image('Position-Weight Matrix', wm_image, step=1)



        return


class gpsValMetrics(keras.callbacks.Callback):
    def __init__(self, val, batchSize, steps, verbose = 1):
        super(gpsValMetrics, self).__init__()
        self.val = val
        self.batchSize = batchSize
        self.steps = steps
        self.score = 'macro'
        self.verbose = verbose


    def on_epoch_end(self, epoch, logs={}):
        total = self.batchSize * self.steps
        step = 0
        val_predict = np.zeros((total))
        val_true = np.zeros((total))


        for batch in self.val.take(self.steps):

            val_data = batch[0]
            val_target = batch[1]

            val_predict[step * self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(self.model.predict(val_data)),axis=1)
            val_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(val_target,axis=1)
            step += 1

        f1 = f1_score(val_true, val_predict, average=self.score)
        recall = recall_score(val_true, val_predict, average=self.score)
        precision =precision_score(val_true, val_predict, average=self.score)

        del val_predict
        del val_true

        if self.verbose:
            print(" - val_f1: %f - val_precision: %f - val_recall: %f" %(f1,precision,recall))

        return


class gpsTestMetrics(keras.callbacks.Callback):
    def __init__(self, test, batchSize, steps, verbose = 1):
        super(gpsTestMetrics, self).__init__()
        self.test = test
        self.batchSize = batchSize
        self.steps = steps
        self.score = 'macro'
        self.verbose = verbose

    def on_test_end(self, logs=None):
        total = self.batchSize * self.steps
        step = 0
        test_predict = np.zeros((total))
        test_true = np.zeros((total))

        for batch in self.test.take(self.steps):

            test_data = batch[0]
            test_target = batch[1]


            test_predict[step*self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(self.model.predict(test_data)),axis=1)
            test_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(test_target,axis=1)
            step += 1

        test_f1 = f1_score(test_true, test_predict, average=self.score)
        test_recall = recall_score(test_true, test_predict, average=self.score)
        test_precision = precision_score(test_true, test_predict, average=self.score)

        del test_predict
        del test_true

        if self.verbose:
            print(" - test_f1: %f - test_precision: %f - test_recall %f" %(test_f1,test_precision,test_recall))

        return


class gpsValTables(keras.callbacks.Callback):
    def __init__(self, val, batchSize, steps, file_writer, motorized=False):

        super(gpsValTables, self).__init__()
        self.val = val
        self.batchSize = batchSize
        self.steps = steps

        self.motorized = motorized
        self.n_classes = 5 if self.motorized else 8

        if self.motorized:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Motorized'
            ]

        else:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Car',
                'Bus',
                'Train',
                'Subway'
            ]

        self.file_writer = file_writer

    def on_train_end(self, logs={}):
        total = self.batchSize * self.steps
        step = 0
        val_predict = np.zeros((total))
        val_true = np.zeros((total))

        for batch in self.val.take(self.steps):

            val_data = batch[0]
            val_target = batch[1]

            pred = self.model.call(val_data)

            val_predict[step*self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(pred),axis=1)
            val_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(val_target,axis=1)

            step += 1

        val_f1 = f1_score(val_true, val_predict, average="macro")
        val_recall = recall_score(val_true, val_predict, average="macro")
        val_precision =precision_score(val_true, val_predict, average="macro")

        print(" - val_f1: %f - val_precision: %f - val_recall: %f" %(val_f1,val_precision,val_recall))

        cm = confusion_matrix(val_true,val_predict)
        global CM
        CM = cm / cm.sum(axis=1)[:, np.newaxis]

        cm_df = pd.DataFrame(cm,
                             index = self.class_names,
                             columns = self.class_names)
        cm_df = cm_df.astype('float') / cm.sum(axis=1)[:, np.newaxis]

        figure = plt.figure(figsize=(10,10))
        sns.heatmap(cm_df, annot = True)
        plt.title('Confusion Matrix')
        plt.ylabel('Actual Values')
        plt.xlabel('Predicted Values')
        cm_image = plot_to_image(figure)

        with self.file_writer.as_default():
            tf.summary.image('Confusion Matrix', cm_image, step=1)

        return


class gpsTestTables(keras.callbacks.Callback):
    def __init__(self,
                 test,
                 batchSize,
                 steps,
                 file_writer,
                 motorized=False):

        super(gpsTestTables, self).__init__()
        self.test = test
        self.batchSize = batchSize
        self.steps = steps

        self.motorized = motorized
        self.n_classes = 5 if self.motorized else 8

        if self.motorized:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Motorized'
            ]

        else:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Car',
                'Bus',
                'Train',
                'Subway'
            ]

        self.file_writer = file_writer

    def on_test_end(self, logs={}):
        total = self.batchSize * self.steps
        step = 0
        test_predict = np.zeros((total))
        test_true = np.zeros((total))

        for batch in self.test.take(self.steps):

            test_data = batch[0]
            test_target = batch[1]

            pred = self.model.call(test_data)

            test_predict[step * self.batchSize: (step + 1) * self.batchSize] = np.argmax(np.asarray(pred), axis=1)
            test_true[step * self.batchSize: (step + 1) * self.batchSize] = np.argmax(test_target, axis=1)

            step += 1

        test_f1 = f1_score(test_true, test_predict, average="macro")
        test_recall = recall_score(test_true, test_predict, average="macro")
        test_precision = precision_score(test_true, test_predict, average="macro")

        print(" - val_f1: %f - val_precision: %f - val_recall: %f" % (test_f1, test_precision, test_recall))

        cm = confusion_matrix(test_true, test_predict)
        global CM
        CM = cm / cm.sum(axis=1)[:, np.newaxis]

        cm_df = pd.DataFrame(cm,
                             index=self.class_names,
                             columns=self.class_names)
        cm_df = cm_df.astype('float') / cm.sum(axis=1)[:, np.newaxis]

        figure = plt.figure(figsize=(10, 10))
        sns.heatmap(cm_df, annot=True)
        plt.title('Confusion Matrix')
        plt.ylabel('Actual Values')
        plt.xlabel('Predicted Values')
        cm_image = plot_to_image(figure)

        with self.file_writer.as_default():
            tf.summary.image('Confusion Matrix', cm_image, step=1)

        return


class accValMetrics(keras.callbacks.Callback):
    def __init__(self, val, batchSize, steps, verbose = 1):
        super(accValMetrics, self).__init__()
        self.val = val
        self.batchSize = batchSize
        self.steps = steps
        self.score = 'macro'
        self.verbose = verbose

    def on_epoch_end(self, epoch, logs={}):
        total = self.batchSize * self.steps
        step = 0
        val_predict = np.zeros((total))
        val_true = np.zeros((total))


        for batch in self.val.take(self.steps):

            val_data = batch[0]
            val_target = batch[1]

            val_predict[step * self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(self.model.predict(val_data, verbose=0)),axis=1)
            val_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(val_target,axis=1)
            step += 1

        f1 = f1_score(val_true, val_predict, average=self.score)
        recall = recall_score(val_true, val_predict, average=self.score)
        precision = precision_score(val_true, val_predict, average=self.score)

        del val_predict
        del val_true

        if self.verbose:
            print(" - val_f1: %f - val_precision: %f - val_recall: %f" %(f1,precision,recall))

        return


class accTestMetrics(keras.callbacks.Callback):
    def __init__(self, test, batchSize, steps, verbose = 1):
        super(accTestMetrics, self).__init__()
        self.test = test
        self.batchSize = batchSize
        self.steps = steps
        self.score = 'macro'
        self.verbose = verbose

    def on_test_end(self, logs=None):
        total = self.batchSize * self.steps
        step = 0
        test_predict = np.zeros((total))
        test_true = np.zeros((total))

        for batch in self.test.take(self.steps):

            test_data = batch[0]
            test_target = batch[1]


            test_predict[step*self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(self.model.predict(test_data, verbose=0)),axis=1)
            test_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(test_target,axis=1)
            step += 1

        test_f1 = f1_score(test_true, test_predict, average=self.score)
        test_recall = recall_score(test_true, test_predict, average=self.score)
        test_precision = precision_score(test_true, test_predict, average=self.score)

        del test_predict
        del test_true

        if self.verbose:
            print(" - test_f1: %f - test_precision: %f - test_recall %f" %(test_f1,test_precision,test_recall))

        return


class accValTables(keras.callbacks.Callback):
    def __init__(self,
                 args,
                 val,
                 batchSize,
                 steps,
                 file_writer,
                 weights_file_writer):

        super(accValTables, self).__init__()
        self.positional = None
        self.weighting = None
        self.val = val
        self.batchSize = batchSize
        self.steps = steps
        self.accBagSize = args.train_args['accBagSize']
        self.MIL = args.train_args['separate_MIL']
        self.bagPositions = args.train_args['train_bag_positions']
        self.random_position = self.bagPositions != 'same'
        self.posPerInstance = 4 if self.bagPositions == 'all' else 1
        self.accBagSize *= self.posPerInstance
        self.motorized = args.train_args['motorized']
        self.n_classes = 5 if self.motorized else 8

        if self.motorized:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Motorized'
            ]

        else:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Car',
                'Bus',
                'Train',
                'Subway'
            ]

        self.file_writer = file_writer
        self.weights_file_writer = weights_file_writer

        if args.data_args['dataset'] == 'CompleteUser1':
            self.pnl = ['Hips']

        else:
            self.pnl = ['Torso', 'Hips', 'Bag', 'Hand']

    def on_train_end(self, logs={}):
        total = self.batchSize * self.steps
        step = 0
        val_predict = np.zeros((total))
        val_true = np.zeros((total))

        if self.MIL and self.random_position:

            self.weighting = keras.models.Model(
                inputs=self.model.input,
                outputs=self.model.get_layer("weight_layer").output
            )

            self.positional = keras.models.Model(
                inputs=self.model.input,
                outputs=self.model.get_layer("positional").output
            )

            weights = np.zeros((total,self.accBagSize))
            positions = np.zeros((total, self.accBagSize))

        for batch in self.val.take(self.steps):

            val_data = batch[0]
            val_target = batch[1]

            pred = self.model.call(val_data)

            val_predict[step*self.batchSize : (step+1)*self.batchSize] = np.argmax(np.asarray(pred),axis=1)
            val_true[step * self.batchSize : (step + 1) * self.batchSize] = np.argmax(val_target,axis=1)

            if self.MIL and self.random_position:
                weights[step * self.batchSize: (step + 1) * self.batchSize] = self.weighting(val_data)
                positions[step * self.batchSize: (step + 1) * self.batchSize] = self.positional(val_data)

            step += 1

        val_f1 = f1_score(val_true, val_predict, average="macro")
        val_recall = recall_score(val_true, val_predict, average="macro")
        val_precision =precision_score(val_true, val_predict, average="macro")

        print(" - val_f1: %f - val_precision: %f - val_recall: %f" %(val_f1,val_precision,val_recall))


        cm = confusion_matrix(val_true,val_predict)
        global CM
        CM = cm/ cm.sum(axis=1)[:, np.newaxis]

        cm_df = pd.DataFrame(cm,
                             index = self.class_names,
                             columns = self.class_names)
        cm_df = cm_df.astype('float') / cm.sum(axis=1)[:, np.newaxis]

        figure = plt.figure(figsize=(10,10))
        sns.heatmap(cm_df, annot = True)
        plt.title('Confusion Matrix')
        plt.ylabel('Actual Values')
        plt.xlabel('Predicted Values')
        cm_image = plot_to_image(figure)

        with self.file_writer.as_default():
            tf.summary.image('Confusion Matrix', cm_image, step=1)


        if self.MIL and self.random_position:

            fig, axs = plt.subplots(ncols=1, figsize=(12, 16))
            fig.suptitle('Weight Matrix')

            for i in range(self.accBagSize):
                wm_pred = np.concatenate([val_predict[:, np.newaxis], positions[:, [i]], weights[:, [i]]], axis=1)
                wm_pred_df = pd.DataFrame(
                    wm_pred,
                    columns=['class', 'position', 'weight']
                )

                if i==0:
                    wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                    wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"], columns=["position"],
                                                fill_value=0)




                    wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                    wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"], columns=["position"],
                                                    fill_value=0)

                else:
                    wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                    wm_pos_sum = wm_pos_sum.add(pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                fill_value=0).values)

                    wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                    wm_pos_count = wm_pos_count.add(pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                  columns=["position"],
                                                  fill_value=0).values)


                if i == self.accBagSize-1:
                    wm_pos = wm_pos_sum.div(wm_pos_count.values)
                    wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                    sns.heatmap(wm_pos, ax=axs, cbar=False, annot=True)
                    fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)
                    axs.set_yticklabels(labels=self.class_names, rotation=45)
                    axs.set_xticklabels(labels=self.pnl)

                    print(wm_pos)

            wm_image = plot_to_image(fig)

            with self.weights_file_writer.as_default():
                tf.summary.image('Weight Matrix', wm_image, step=1)

        return


class accTestTables(keras.callbacks.Callback):
    def __init__(self,
                 args,
                 test,
                 batchSize,
                 steps,
                 file_writer,
                 weights_file_writer,
                 std_file_writer):

        super(accTestTables, self).__init__()
        self.test = test
        self.batchSize = batchSize
        self.steps = steps
        self.accBagSize = args.train_args['accBagSize']
        self.MIL = args.train_args['separate_MIL']
        self.bagPositions = args.train_args['test_bag_positions']
        self.random_position = self.bagPositions == 'random'
        self.posPerInstance = 4 if self.bagPositions == 'all' else 1
        self.accBagSize *= self.posPerInstance
        self.user = args.train_args['test_user']

        self.motorized = args.train_args['motorized']
        self.n_classes = 5 if self.motorized else 8

        if self.motorized:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Motorized'
            ]

        else:
            self.class_names = [
                'Still',
                'Walking',
                'Run',
                'Bike',
                'Car',
                'Bus',
                'Train',
                'Subway'
            ]

        self.file_writer = file_writer
        self.weights_file_writer = weights_file_writer
        self.std_file_writer = std_file_writer

        if args.data_args['dataset'] == 'CompleteUser1':
            self.pnl = ['Hips']

        else:
            self.pnl = ['Torso', 'Hips', 'Bag', 'Hand']

        self.path = os.path.join("saves", "Weights-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            os.makedirs(self.path)
        except OSError as e:
            print("Error: %s - %s." % (e.filename, e.strerror))

    def on_test_end(self, logs={}):
        total = self.batchSize * self.steps
        step = 0
        test_predict = np.zeros((total))
        test_true = np.zeros((total))

        if self.MIL:
            self.weighting = keras.models.Model(
                inputs=self.model.input,
                outputs=self.model.get_layer("weight_layer").output
            )

            weights = np.zeros((total, self.accBagSize))

        if self.random_position:
            self.positional = keras.models.Model(
                inputs=self.model.input,
                outputs=self.model.get_layer("positional").output
            )

            positions = np.zeros((total, self.accBagSize))

        for batch in self.test.take(self.steps):

            test_data = batch[0]
            test_target = batch[1]

            pred = self.model.call(test_data)

            test_predict[step * self.batchSize: (step + 1) * self.batchSize] = np.argmax(np.asarray(pred), axis=1)
            test_true[step * self.batchSize: (step + 1) * self.batchSize] = np.argmax(test_target, axis=1)

            if self.MIL:
                weights[step * self.batchSize: (step + 1) * self.batchSize] = self.weighting(test_data)

            if self.random_position:
                positions[step * self.batchSize: (step + 1) * self.batchSize] = self.positional(test_data)

            step += 1

        test_f1 = f1_score(test_true, test_predict, average="macro")
        test_recall = recall_score(test_true, test_predict, average="macro")
        test_precision = precision_score(test_true, test_predict, average="macro")

        print(" - val_f1: %f - val_precision: %f - val_recall: %f" % (test_f1, test_precision, test_recall))

        cm = confusion_matrix(test_true, test_predict)
        global CM
        CM = cm / cm.sum(axis=1)[:, np.newaxis]

        cm_df = pd.DataFrame(cm,
                             index=self.class_names,
                             columns=self.class_names)
        cm_df = cm_df.astype('float') / cm.sum(axis=1)[:, np.newaxis]

        figure = plt.figure(figsize=(10, 10))
        sns.heatmap(cm_df, annot=True)
        plt.title('Confusion Matrix')
        plt.ylabel('Actual Values')
        plt.xlabel('Predicted Values')
        cm_image = plot_to_image(figure)

        with self.file_writer.as_default():
            tf.summary.image('Confusion Matrix', cm_image, step=1)

        if self.MIL:
            modes = test_predict[:, np.newaxis]
            ws = np.concatenate([weights[:, [i]] for i in range(self.accBagSize)], axis=1)
            ws_std = ws.std(axis=1)[:, np.newaxis]
            std_mode = np.concatenate([modes, ws_std],  axis=1)
            std_df = pd.DataFrame(
                std_mode,
                columns=['class', 'std']
            )
            std_df = std_df.groupby(['class'], as_index=False).mean()

            with self.std_file_writer.as_default():
                tf.summary.text('Weights Std.', pd.DataFrame.to_string(std_df), step=1)

            print(std_df)

            wmStdFile = os.path.join(self.path, "WeightStd-" + str(self.user) + ".csv")
            std_df.to_csv(wmStdFile, index=False)

        if self.MIL and self.random_position:

            fig, axs = plt.subplots(ncols=1, figsize=(12, 16))
            fig.suptitle('Weight Matrix')

            for i in range(self.accBagSize):
                wm_pred = np.concatenate([test_predict[:, np.newaxis], positions[:, [i]], weights[:, [i]]], axis=1)
                wm_pred_df = pd.DataFrame(
                    wm_pred,
                    columns=['class', 'position', 'weight']
                )

                if i==0:
                    wm_pos_sum = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                    wm_pos_sum = pd.pivot_table(wm_pos_sum, values="weight", index=["class"], columns=["position"],
                                                fill_value=0)

                    wm_pos_count = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                    wm_pos_count = pd.pivot_table(wm_pos_count, values="size", index=["class"], columns=["position"],
                                                    fill_value=0)

                else:
                    wm_pos_sum_ = wm_pred_df.groupby(['class', 'position'], as_index=False).sum()
                    wm_pos_sum = wm_pos_sum.add(pd.pivot_table(wm_pos_sum_, values="weight", index=["class"], columns=["position"],
                                                fill_value=0).values)

                    wm_pos_count_ = wm_pred_df.groupby(['class', 'position']).size().to_frame(name='size').reset_index()
                    wm_pos_count = wm_pos_count.add(pd.pivot_table(wm_pos_count_, values="size", index=["class"],
                                                  columns=["position"],
                                                  fill_value=0).values)


                if i == self.accBagSize-1:
                    wm_pos = wm_pos_sum.div(wm_pos_count.values)
                    wm_pos = wm_pos.div(wm_pos.sum(axis=1), axis=0)

                    sns.heatmap(wm_pos, ax=axs, cbar=False, annot=True)
                    fig.colorbar(axs.collections[0], ax=axs, location="right", use_gridspec=False, pad=0.2)
                    axs.set_yticklabels(labels=self.class_names, rotation=45)
                    axs.set_xticklabels(labels=self.pnl)

                    wmPosFile = os.path.join(self.path, "PositionWeights-" + str(self.user) + ".csv")
                    wm_pos.to_csv(wmPosFile, index=False)
                    print(wm_pos)

            wm_image = plot_to_image(fig)

            with self.weights_file_writer.as_default():
                tf.summary.image('Weight Matrix', wm_image, step=1)

        return






