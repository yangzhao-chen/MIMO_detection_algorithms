import tensorflow as tf 

class MMNet(object):
    def __init__(self, params):
        self.Nr = params['Nr']
        self.Nt = params['Nt']
        self.H = tf.compat.v1.placeholder(shape=[None, 2*self.Nr, 2*self.Nt], dtype=tf.float32)
        self.y = tf.compat.v1.placeholder(shape=[None, 2*self.Nr], dtype=tf.float32)
        self.x = tf.compat.v1.placeholder(shape=[None, 2*self.Nt], dtype=tf.float32)
        self.noise_sigma2 = tf.compat.v1.placeholder(shape=(None, 1), dtype=tf.float32)
        self.batch_size = params['batch_size']
        self.L = params['MMNet_layer']
        self.constellation = params['constellation']
        self.M = tf.shape(params['constellation'])[0]
        self.learning_rate = params['learning_rate']
    
    def creat_graph(self):   
        xhatk = tf.zeros(shape=[self.batch_size, 2*self.Nt], dtype=tf.float32)
        rk = self.y
        xhat = []
        for k in range(1, self.L+1):
            xhatk, rk, wr = self.layer(xhatk, rk)
            xhat.append(xhatk)
        print("Total number of trainable variables in MMNet", self.get_n_vars())
        loss = self.loss_fun(xhat, self.x)
        print('3', loss)
        train_step = tf.compat.v1.train.AdamOptimizer(self.learning_rate).minimize(tf.reduce_mean(loss))
        x_hatk_idx = self.demodulate(xhatk)
        indices = self.demodulate(self.x)
        accuracy = self.accuracy(indices, x_hatk_idx)
        ser = 1-accuracy
        MMNet_nodes = {
            'xhat': xhatk, 
            'ser': ser,
            'x': self.x,
            'y': self.y,
            'H': self.H,
            'noise_sigma2': self.noise_sigma2,
            'M': self.M,
            'train': train_step,
            'loss': loss,
            'wr': wr,
        }
        return MMNet_nodes

    def loss_fun(self, xhat, x):
        """
        损失函数1，每一层输出的x的估计xhatk与x的均方差之和
        Input: 
        xhat: 每一层输出的x的估计，是一个包含L个元素的列表，每个元素是Tensor(shape=(batch_size, 2*Nt), dtype=tf.float32)
        x: 发送调制符号x Tensor(shape=(batch_size,2*Nt), dtype=float32)
        Output:
        loss: 损失值 Tensor(shape=(), dtype=float32)
        """
        loss = 0
        for xhatk in xhat:
            lk = tf.compat.v1.losses.mean_squared_error(labels=x, predictions=xhatk)
            loss += lk
            tf.compat.v1.add_to_collection(tf.compat.v1.GraphKeys.LOSSES, lk)
        return loss

    def get_n_vars(self):
        total_parameters = 0
        for variable in tf.compat.v1.trainable_variables():
            shape = variable.get_shape()
            variable_parameters = 1
            for dim in shape:
                variable_parameters *= dim.value
            total_parameters += variable_parameters
        return total_parameters
    
    def layer(self, xhatt, rt):
        # 线性
        Wr = tf.compat.v1.Variable(tf.random.normal(shape=[1, self.Nt, self.Nr], mean=0.0, stddev=0.001))
        # Wi = tf.compat.v1.Variable(tf.random.normal(shape=[1, self.Nt, self.Nr], mean=0.0, stddev=0.001))
        # W = tf.concat([tf.concat([Wr, -Wi], axis=2), tf.concat([Wi, Wr], axis=2)], axis=1)
        W = tf.compat.v1.Variable(tf.random.normal(shape=[1, 2*self.Nt, 2*self.Nr], mean=0.0, stddev=0.001))
        W_t = tf.tile(W, [self.batch_size, 1, 1])
        zt = xhatt + self.batch_matvec_mul(W_t, rt)

        # 非线性
        HTH = tf.matmul(self.H, self.H, transpose_a=True)
        v2_t = tf.divide(tf.reduce_sum(tf.square(rt), axis=1, keepdims=True)-tf.cast(2*self.Nr, tf.float32) *
                         self.noise_sigma2 / 2, tf.expand_dims(tf.linalg.trace(HTH), axis=1))
        v2_t = tf.maximum(v2_t, 1e-9)
        v2_t = tf.expand_dims(v2_t, axis=1)
        C_t = tf.eye(2*self.Nt, batch_shape=[self.batch_size]) - tf.matmul(W_t, self.H)
        tau2_t = 1./tf.cast(2*self.Nt, tf.float32) *\
                 tf.reshape(tf.linalg.trace(tf.matmul(C_t, C_t, transpose_b=True)), [-1, 1, 1])\
                 * v2_t + 1./tf.cast(2*self.Nt, tf.float32)*tf.reshape(self.noise_sigma2, [-1, 1, 1])\
                 * tf.reshape(tf.linalg.trace(tf.matmul(W_t, W_t, transpose_b=True)), [-1, 1, 1])
        tau2_t = tau2_t / tf.Variable(tf.random.normal([1, 2*self.Nt, 1], mean=1, stddev=0.1)) 
        xhatt = self.gaussian(zt, tau2_t)
        rt = self.y - self.batch_matvec_mul(self.H, xhatt)
        return xhatt, rt, Wr

    def gaussian(self, zt, tau2_t):
        tau2_t = tau2_t
        print('1', zt, self.constellation)
        arg = tf.reshape(zt, [-1, 1]) - tf.reshape(self.constellation, [1, -1])
        print('2', arg)
        arg = tf.reshape(arg, [-1, 2*self.Nt, self.M])
        arg = - (tf.square(arg) / 2. / tau2_t)
        arg = tf.reshape(arg, [-1, self.M])
        shatt1 = tf.nn.softmax(arg, axis=1)
        shatt1 = tf.matmul(shatt1, tf.reshape(self.constellation, [self.M, 1]))
        shatt1 = tf.reshape(shatt1, [-1, 2*self.Nt])
        return shatt1
    
    def batch_matvec_mul(self, A, b, transpose_a=False):
        """
        矩阵A与矩阵b相乘，其中A.shape=(batch_size, Nr, Nt)
        b.shape = (batch_size, Nt)
        输出矩阵C，C.shape = (batch_size, Nr)
        """
        C = tf.matmul(A, tf.expand_dims(b, axis=2), transpose_a=transpose_a)
        return tf.squeeze(C, -1)
    
    def accuracy(self, x, y):
        """
        Computes the fraction of elements for which x and y are equal
        """
        return tf.reduce_mean(tf.cast(tf.equal(x,y), tf.float32))

    def demodulate(self, x):
        """
        信号解调(复数域解调）
        Input:
        y: 检测器检测后的信号 Tensor(shape=(batchsize, 2*Nt), dtype=float32)
        constellation: 星座点，即发送符号可能的离散值 Tensor(shape=(np.sqrt(调制阶数), ), dtype=float32)
        Output:
        indices: 解调后的基带数据信号，Tensor(shape=(batchsize, Nt)， dtype=tf.int32)
        """
        # shape_x = x.shape
        # print('shape_x', shape_x)
        # print('x.shape', x.shape)
        # print('x', x[:, 0:self.Nt], x[:, self.Nt: self.Nt * 2])
        # print(tf.complex(x[:, 0: self.Nt], x[:, self.Nt: self.Nt * 2]))
        # print(self.x, self.y)
        # print(shape_x, tf.cast(tf.divide(shape_x[1], 2), tf.int32))
        x_complex = tf.complex(x[:, 0: self.Nt], x[:, self.Nt: self.Nt * 2])
        x_complex = tf.reshape(x_complex, shape=[-1, 1])
        constellation = tf.reshape(self.constellation, [1, -1])
        constellation_complex = tf.reshape(tf.complex(constellation, 0.)
                                           - tf.complex(0., tf.transpose(constellation)), [1, -1])
        indices = tf.cast(tf.argmin(tf.abs(x_complex - constellation_complex), axis=1), tf.int32)
        indices = tf.reshape(indices, shape=tf.shape(x_complex))
        return indices

        
        

