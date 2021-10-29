# Combined form of the AD_Process and AD_Train classes, to be fed into the HPC cluster at max sample size
# Richard Masson
print("IMPLEMENTATION: NEO")
print("CURRENT TEST: NEO model, only two classes, and shift augmentation.")
from nibabel import test
import LabelReader as lr
import NIFTI_Engine as ne
import numpy as np
import random
from sklearn.model_selection import train_test_split
import tensorflow as tf
print(tf.version.VERSION)
from scipy import ndimage
from tensorflow import keras
from tensorflow.keras import layers
from tensorflow.python.keras.callbacks import EarlyStopping, ModelCheckpoint, TensorBoard
from matplotlib import pyplot as plt
import random
import datetime
from collections import Counter

# Are we in testing mode?
testing_mode = False

# Attempt to better allocate memory.
gpus = tf.config.experimental.list_physical_devices('GPU')
for gpu in gpus:
  tf.config.experimental.set_memory_growth(gpu, True)

# Define image size (lower image resolution in order to speed up for broad testing)
if testing_mode:
    scale = 1
else:
    scale = 1 # Quarter the size of optimal
w = 128/scale
h = 128/scale
d = 64/scale

# Fetch all our seperated data
#x_arr, scan_meta = ne.extractArrays('all', root="/home/rmasson/Documents/Data") # Linux workstation world
rootloc = "/scratch/mssric004/Data" # HPC world
if testing_mode:
    rootloc = "/scratch/mssric004/Data_Tiny"
    print("TEST MODE ENABLED.")
x_arr, scan_meta = ne.extractArrays('all', w, h, d, root=rootloc)
clinic_sessions, cdr_meta = lr.loadCDR()

# Generate some cdr y labels for each scan
# Current plan: Use the cdr from the closest clinical entry by time (won't suffice in the longterm but it will do for now)
y_arr = []
for scan in scan_meta:
    scan_day = scan['day']
    scan_cdr = -1
    cdr_day = -1
    min = 100000
    try:
        for x in cdr_meta[scan['ID']]:
            diff = abs(scan_day-x[0])
            if diff < min:
                min = diff
                scan_cdr = x[1]
                cdr_day = x[0]
        scaled_val = int(scan_cdr*2) #0 = 0, 0.5 = 1, 1 = 2 (elimate decimals)
        #if scaled_val > 2:
        #    scaled_val = 2 # Cap out at 2 since we can classify anything at 1 or above as "severe"
        if scaled_val > 1: # Temporary approach to use only two classes to reduce complexity FOR NOW
            scaled_val = 1
        y_arr.append(scaled_val) 
    except KeyError as k:
        print(k, "| Seems like the entry for that patient doesn't exist.")
classNo = len(np.unique(y_arr))
print("There are", classNo, "unique classes. ->", np.unique(y_arr), "in the dataset.")
classCount = Counter(y_arr)
print("Class count:", classCount)
y_arr = tf.keras.utils.to_categorical(y_arr)

# Split data
if testing_mode:
    x_train, x_val, y_train, y_val = train_test_split(x_arr, y_arr) # ONLY USING WHILE THE SET IS TOO SMALL FOR STRATIFICATION
    x_val, x_test, y_val, y_test = train_test_split(x_val, y_val, test_size=0.2) # ALSO TESTING BRANCH NO STRATIFY LINE
else:
    x_train, x_val, y_train, y_val = train_test_split(x_arr, y_arr, stratify=y_arr) # Defaulting to 75 train, 25 val/test. Also shuffle=true and stratifytrue.
    x_val, x_test, y_val, y_test = train_test_split(x_val, y_val, stratify=y_val, test_size=0.2) # 80/20 val/test, therefore 75/20/5 train/val/test.

if testing_mode:
    np.savez_compressed('testing_sub', a=x_test, b=y_test)
else:
    np.savez_compressed('testing_neo', a=x_test, b=y_test)
'''
# Ascertain what the class breakdown is
print("Class breakdowns:")
print("Training:", collections.Counter(y_train))
print("Validation:", collections.Counter(y_val))
print("Testing:", collections.Counter(y_test))
print("Data has been preprocessed. Moving on to model...")
'''
# Model architecture go here
def gen_model(width=128, height=128, depth=64, classes=3): # Make sure defaults are equal to image resizing defaults
    # Initial build version - no explicit Sequential definition
    inputs = keras.Input((width, height, depth, 1)) # Added extra dimension in preprocessing to accomodate that 4th dim

    # Contruction seems to be pretty much the same as if this was 2D. Kernal should default to 3,3,3
    x = layers.Conv3D(filters=8, kernel_size=3, activation="relu")(inputs) # Layer 1: Usual 64 filter start
    x = layers.BatchNormalization()(x)
    x = layers.MaxPool3D(pool_size=2)(x) # Usually max pool after the conv layer

    x = layers.Conv3D(filters=16, kernel_size=3, activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPool3D(pool_size=2)(x)

    x = layers.Conv3D(filters=32, kernel_size=3, activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPool3D(pool_size=2)(x)

    x = layers.Conv3D(filters=64, kernel_size=3, activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPool3D(pool_size=2)(x)
    '''
    x = layers.Conv3D(filters=128, kernel_size=3, activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPool3D(pool_size=2)(x)
    '''
    x = layers.GlobalAveragePooling3D()(x)
    x = layers.Dropout(0.5)(x)
    #x = layers.Dense(units=1300)(x)
    x = layers.Dense(units=512, activation="relu")(x) # Implement a simple dense layer with double units
    x = layers.Dense(units=64, activation="relu")(x)

    outputs = layers.Dense(units=classes, activation="softmax")(x) # Units = no of classes. Also softmax because we want that probability output

    # Define the model.
    model = keras.Model(inputs, outputs, name="3DCNN")

    return model

# Model hyperparameters
if testing_mode:
    epochs = 3
    batches = 2
else:
    epochs = 50 # Small for testing purposes
    batches = 8 # Going to need to fiddle with this over time (balance time save vs. running out of memory)

# Data augmentation functions
@tf.function
def rotate(image):
    def scipy_rotate(image): # Rotate by random angular amount
        # define some rotation angles
        angles = [-20, -10, -5, 5, 10, 20]
        # pick angles at random
        angle = random.choice(angles)
        # rotate image
        image = ndimage.rotate(image, angle, reshape=False)
        image[image < 0] = 0
        image[image > 1] = 1
        return image

    augmented_image = tf.numpy_function(scipy_rotate, [image], tf.float32)
    return augmented_image
@tf.function
def shift(image):
    def scipy_shift(image): # Shift by some small amount
        # generate random x shift pixel value
        x = (int)(random.uniform(-15, 15))
        # generate random y shift pixel value
        y = (int)(random.uniform(-15, 15))
        image = ndimage.interpolation.shift(image, (x, y, 0), mode='nearest')
        image[image < 0] = 0
        image[image > 1] = 1
        return image

    augmented_image = tf.numpy_function(scipy_shift, [image], tf.float32)
    return augmented_image

def train_preprocessing(image, label): # Only use for training, as it includes rotation augmentation
    # Shift image
    image = shift(image)
    # Rotate image
    image = rotate(image)
    image = tf.expand_dims(image, axis=3)
    return image, label

def validation_preprocessing(image, label): # Can be used for val or test data (just ensures the dimensions are ok for the model)
    """Process validation data by only adding a channel."""
    image = tf.expand_dims(image, axis=3)
    return image, label

# Augment data, as well expand dimensions to make the training model accept it (by adding a 4th dimension)
train_loader = tf.data.Dataset.from_tensor_slices((x_train, y_train))
validation_loader = tf.data.Dataset.from_tensor_slices((x_val, y_val))
batch_size = batches
# Augment the on the fly during training.
train_set = (
    train_loader.shuffle(len(x_train))
    .map(train_preprocessing)
    .batch(batch_size)
    .prefetch(batch_size)
)
# Only rescale.
validation_set = (
    validation_loader.shuffle(len(x_val))
    .map(validation_preprocessing)
    .batch(batch_size)
    .prefetch(batch_size)
)

# Build model.
model = gen_model(width=128, height=128, depth=64, classes=classNo)
model.summary()
optim = keras.optimizers.Adam(learning_rate=0.001) # LR chosen based on principle but double-check this later
# Note: These things will have to change if this is changed into a regression model
model.compile(optimizer=optim, loss='binary_crossentropy', metrics=['accuracy']) # Binary right now since only 2 classes.

# Checkpointing & Early Stopping
es = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
checkpointname = "weight_history_NEO.h5"
if testing_mode:
    checkpointname = "weight_history_testing.h5"
mc = ModelCheckpoint(checkpointname, monitor='val_accuracy', mode='max', verbose=1, save_best_only=False)
log_dir = "/scratch/mssric004/logs/fit/" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
tb = TensorBoard(log_dir=log_dir, histogram_freq=1)

# Class weighting
# Data distribution is {0: 2625, 1: 569, 2: 194}
# So if we want to do this in equal ratios it would {be 0:1., 1:5., 2:13.}
class_weight = {0: 1., 1: 4.}

# Run the model
print("Fitting model...")
history = model.fit(train_set, validation_data=validation_set, batch_size=batches, epochs=epochs, shuffle=True, verbose=1, callbacks=[es, mc, tb], class_weight=class_weight)
# Note: Add early stop back in at some point
modelname = "ADModel_NEO"
if testing_mode:
    modelname = "ADModel_Testing"
model.save(modelname)
print(history.history)
actual_epochs = len(history.history['val_loss'])
print("Complete. Ran for ", actual_epochs, "/", epochs, " epochs.\nParameters saved to", modelname)
for i in range(actual_epochs):
    print("Epoch", i+1, ": Loss [", history.history['loss'][i], "] Val Loss [", history.history['val_loss'][i])

from sklearn.metrics import classification_report

# Generate a classification matrix
print("Generating classification report...\n")
Y_test = np.argmax(y_test, axis=1)
y_pred = model.predict(np.expand_dims(x_test, axis=-1), batch_size=2)
y_pred = np.argmax(y_pred, axis=1)
print("Actual test set:")
print(Y_test)
print("Predictions are  as follows:")
print(y_pred)
print(classification_report(Y_test, y_pred))

print("Done.")
