import tensorflow as tf
import numpy as np

# from hmm import HMM as HMM_Orig


class HMM(object):
    """
    A class for Hidden Markov Models.

    Assumes measurements are Gaussian distributed

    The model attributes are:
    - K :: the number of states
    - w :: the K Gaussian output distribution means
    - sigma :: the K standard deviations of Gaussian output
    - P :: the K by K transition matrix (from state i to state j,
        (i, j) in [1..K])
    - p0 :: the initial distribution (defaults to starting in state 0)

    Notes:
    - by convention, we specify w[0] as the 'Off' state, although this
      is not required.
    """

    def __init__(self, w, P, p0=None):
        self.w = np.array(w)
        assert self.w.ndim == 1
        self.K = w.shape[0]

        if P.shape != (self.K, self.K):
            raise ValueError(
                'dimensions of P {} must match w {}'.
                format(P.shape, self.K))
        self.P = P
        self.logP = np.log(self.P)

        if p0 is None:
            self.p0 = np.ones(self.K)
            self.p0 /= sum(self.p0)
        elif len(p0) != self.K:
            raise ValueError(
                'dimensions of p0 {} must match w {}'.format(
                    p0.shape, w.shape))
        else:
            self.p0 = p0
        self.logp0 = np.log(self.p0)

    def log_lik(self, y):
        # scale factor ...
        return 2 * -np.abs(y - self.w)

    def lik(self, y):
        # the output of this will not necessarily sum to 1.  An easy example is
        # if y is some very unlikely value.  In this case, the sum may be
        # significantly less than 1
        return np.exp(self.log_lik(y))


class HMMNumpy(HMM):

    def forward_backward(self, y):
        # set up
        nT = y.size
        posterior = np.zeros((nT, self.K))
        forward = np.zeros((nT + 1, self.K))
        backward = np.zeros((nT + 1, self.K))

        # forward pass
        forward[0, :] = 1.0 / self.K
        for t in xrange(nT):
            tmp = np.multiply(
                np.matmul(forward[t, :], self.P),
                self.lik(y[t])
            )

            forward[t + 1, :] = tmp / np.sum(tmp)

        # backward pass
        backward[-1, :] = 1.0 / self.K
        for t in xrange(nT, 0, -1):
            tmp = (
                np.matrix(self.P) *
                np.diag(self.lik(y[t - 1])) *
                np.matrix(backward[t, :]).transpose()
            ).transpose()

            backward[t - 1, :] = tmp / np.sum(tmp)

        # remove initial/final probabilities
        forward = forward[1:,:]
        backward = backward[:-1,:]

        # combine and normalize
        posterior = np.array(forward) * np.array(backward)
        # [:,None] expands num to be correct size
        posterior = posterior / np.sum(posterior, 1)[:,None]

        return posterior, forward, backward

    def _viterbi_partial_forward(self, scores):
        tmpMat = np.zeros((self.K, self.K))
        for i in range(self.K):
            for j in range(self.K):
                tmpMat[i, j] = scores[i] + self.logP[i, j]
        return tmpMat

    def viterbi_decode(self, y):
        y = np.array(y)

        nT = y.shape[0]

        pathStates = np.zeros((nT, self.K), dtype=np.int)
        pathScores = np.zeros((nT, self.K))

        # initialize
        pathScores[0] = self.logp0 + self.log_lik(y[0])

        for t, yy in enumerate(y[1:]):
            # propagate forward
            tmpMat = self._viterbi_partial_forward(pathScores[t])

            # the inferred state
            pathStates[t + 1] = np.argmax(tmpMat, 0)
            pathScores[t + 1] = np.max(tmpMat, 0) + self.log_lik(yy)

        # now backtrack viterbi to find states
        s = np.zeros(nT, dtype=np.int)
        s[-1] = np.argmax(pathScores[-1])
        for t in range(nT - 1, 0, -1):
            s[t - 1] = pathStates[t, s[t]]

        return s, pathScores


class HMMTensorflow(HMM):

    def forward_backward(self, y):
        # set up
        nT = y.size
        posterior = np.zeros((nT, self.K))
        forward = []
        backward = np.zeros((nT + 1, self.K))

        # forward pass
        forward.append(
            tf.ones((1, self.K), dtype=tf.float64) * (1.0 / self.K)
        )
        for t in xrange(nT):
            # NOTE: np.matrix expands forward[t, :] into 2d and causes * to be
            # matrix multiplies instead of element wise that an array would be
            tmp = tf.mul(
                tf.matmul(forward[t], self.P),
                self.lik(y[t])
            )

            forward.append(tmp / tf.reduce_sum(tmp))

        # backward pass
        backward = [None] * (nT + 1)
        backward[-1] = tf.ones((1, self.K), dtype=tf.float64) * (1.0 / self.K)
        for t in xrange(nT, 0, -1):
            tmp = tf.transpose(
                tf.matmul(
                    tf.matmul(self.P, tf.diag(self.lik(y[t - 1]))),
                    tf.transpose(backward[t])
                )
            )
            backward[t - 1] = tmp / tf.reduce_sum(tmp)

        # remove initial/final probabilities
        forward = forward[1:]
        backward = backward[:-1]

        # combine and normalize
        posterior = [f * b for f, b in zip(forward, backward)]
        posterior = [p / tf.reduce_sum(p) for p in posterior]

        return posterior, forward, backward

    def _viterbi_partial_forward(self, scores):
        # first convert scores into shape [K, 1]
        # then concatenate K of them into shape [K, K]
        expanded_scores = tf.concat(
            1, [tf.expand_dims(scores, 1)] * self.K
        )
        return expanded_scores + self.logP

    def viterbi_decode(self, y, nT):
        # pathStates and pathScores wil be of type tf.Tensor.  They
        # are lists since tensorflow doesn't allow indexing, and the
        # list and order are only really necessary to build the unrolled
        # graph.  We never do any computation across all of time at once
        pathStates = []
        pathScores = []

        # initialize
        pathStates.append(None)
        pathScores.append(self.logp0 + self.log_lik(y[0]))

        for t, yy in enumerate(y[1:]):
            # propagate forward
            tmpMat = self._viterbi_partial_forward(pathScores[t])

            # the inferred state
            pathStates.append(tf.argmax(tmpMat, 0))
            pathScores.append(tf.reduce_max(tmpMat, 0) + self.log_lik(yy))

        # now backtrack viterbi to find states
        s = [0] * nT
        s[-1] = tf.argmax(pathScores[-1], 0)
        for t in range(nT - 1, 0, -1):
            s[t - 1] = tf.gather(pathStates[t], s[t])

        return s, pathScores


def gradient_hmm():
    # P = np.array([[0.5, 0.5], [0.0000000000001, 0.9999999999999]])
    P = np.array([[0.5, 0.5], [0.0, 1.0]])
    # P = np.array([[0.9999, 0.0001], [0.0, 1.0]])
    w = np.array([0., 1.0])

    hmm = HMMTensorflow(w, P)

    x = [0, 1, 0, 0, 1, 1, 1, 1, 1]
    y = [0, 0, 0, 0, 1, 1, 1, 1, 1]

    nT = len(y)

    x = [tf.Variable(e) for e in np.array(x, dtype=np.int64)]
    y = np.array(y, dtype=np.int64)

    outstates, outscores = hmm.viterbi_decode(y, nT)

    print y
    print outstates
    errors = [
        yi - outstatesi
        for yi, outstatesi in zip(y, outstates)
    ]

    error_squared = [
        tf.square(error) for error in errors
    ]

    # use python sum since error_squared is a python list of 0d tensors
    loss = tf.cast(sum(error_squared), tf.float64) / len(error_squared)

    print 'x', x
    print 'loss', loss
    print 'gradients'
    # print tf.gradients(x[0] * 2, x[0])
    print tf.gradients(outstates, x)
    print tf.gradients(loss, x)


if __name__ == "__main__":
    gradient_hmm()
    import sys
    sys.exit()

    # P = np.array([[0.5, 0.5], [0.0000000000001, 0.9999999999999]])
    P = np.array([[0.5, 0.5], [0.0, 1.0]])
    # P = np.array([[0.9999, 0.0001], [0.0, 1.0]])
    w = np.array([0., 1.0])

    hmm = HMMNumpy(w, P)

    # y = np.zeros(10)
    # y[4:] += 1.0
    # y += 0.5 * (np.random.random(10) - 0.5)
    # y[-4:] = 0
    y = [0, 1, 0, 0, 1, -109, 1, 1, 1]

    # y = np.array([1, 0])

    print ', '.join(['%.02f' % e for e in y])

    outstates, outscores = hmm.viterbi_decode(y)
    print 'y', y
    print 'outstates', outstates
    print 'outscores'
    print outscores

    # TODO
    #   - replace y with direct log_lik
    #   X convert to tensorflow
    #   - more comments
    #   - extract common functions into base class
    #   - implement forward-backward
    #   - visualize gradient
    #   - possible that code could be simplified once model is solid ... later
