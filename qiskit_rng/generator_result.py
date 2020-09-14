# -*- coding: utf-8 -*-

# This code is part of Qiskit.
#
# (C) Copyright IBM 2020.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

# (C) Copyright CQC 2020.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the Programs
# directory of this source or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Generator job result."""

from typing import List, Optional, Callable, Tuple
from math import floor

from .utils import (bell_value, generate_wsr, get_extractor_bits, bitarray_to_bytes,
                    h_mins, na_set, dodis_output_size, hayashi_parameters)
from .model import CQCExtractorParams


class GeneratorResult:
    """Representation of random number sampling result."""

    def __init__(
            self,
            wsr: List[List[int]],
            raw_bits_list: List[List[int]]
    ) -> None:
        """GeneratorResult constructor.

        Bell values are calculated based on the input parameters. The values include:

            * mermin_correlator: Mermin correlator value. The statistical value
              calculated from the probability distributions.
              This verifies quantum correlations when >2, as 2 is the maximal value possible
              in a classical setup.
            * winning_probability: Probability of "winning" each round in the Mermin
              quantum game. It is used to verify quantum correlations (the maximum
              probability in a classical implementation is 87.5%).
            * losing_probability: 1-`winning_probability`.

        Args:
            wsr: WSR used to generate the circuits.
            raw_bits_list: A list of formatted bits from job results.
        """
        self.wsr = wsr
        self._raw_bits_list = raw_bits_list
        self.raw_bits = [bit for sublist in raw_bits_list for bit in sublist]

        self.losing_probability, self.winning_probability, self.mermin_correlator = \
            bell_value(wsr, raw_bits_list)

    def bell_values(self) -> Tuple[float, float, float]:
        """Return a tuple of the bell values.

        Returns:
            The losing probability, winning probability, and Mermin correlator.
        """
        return self.losing_probability, self.winning_probability, self.mermin_correlator

    def get_cqc_extractor_params(
            self,
            rate_sv: float = 0.95,
            expected_correlator: Optional[float] = None,
            epsilon_sec: float = 1e-30,
            quantum_proof: bool = False,
            trusted_backend: bool = True,
            privacy: bool = False,
            wsr_generator: Optional[Callable] = None
    ) -> CQCExtractorParams:
        """Return parameters for the CQC extractors.

        Dodis is the first 2-source extractor that takes the Bell value
        and the WSR, in order to generate high-quality random bits.

        Hayashi is the second extractor. It takes the output of the first extractor
        and another WSR string to increase the size of the final output string.
        The second extractor is only used if `trusted_backend` is ``True`` and
        `privacy` is ``False``.

        Args:
            rate_sv: Assumed randomness rate of the initial WSR as a Santha-Vazirani source.
            expected_correlator: The expected correlator value.
                :data:`qiskit_rng.constants.EXPECTED_CORRELATOR`
                contains known values for certain backends. If ``None``, the observed value
                from the sampling output is used.
            epsilon_sec: The distance to uniformity of the final bit string. When performing
                privacy amplification as well, this is the distance to a perfectly
                uniform and private string.
            quantum_proof: Set to ``True`` for quantum-proof extraction in the Markov
                model (most secure), ``False`` for classical-proof extraction in the
                standard model. Note that setting this to ``True`` reduces the generation
                rates considerably.
            trusted_backend: ``True`` if the raw bits were generated by a trusted
                backend and communicated securely.
            privacy: ``True`` if privacy amplification is to be performed.
            wsr_generator: Function used to generate WSR. It must take the
                number of bits as the input and a list of random bits (0s and 1s)
                as the output.

        Returns:
            A ``CQCExtractorParams`` instance that contains all the parameters
            needed for the extractors.

        Raises:
            ValueError: If an input argument is invalid.
        """
        expected_correlator = expected_correlator or self.mermin_correlator

        if self.mermin_correlator < expected_correlator:
            raise ValueError("Observed correlator value {} is lower than expected value {}. "
                             "Rerun with a larger sample size or use a different backend.".format(
                                 self.mermin_correlator, expected_correlator))

        if privacy and not trusted_backend:
            raise ValueError("Cannot perform privacy amplification using a untrusted backend.")

        if wsr_generator is None:
            wsr_generator = generate_wsr

        correlator = expected_correlator
        losing_prob = (4-correlator)/16

        bits = get_extractor_bits(self._raw_bits_list)
        num_bits = len(bits)
        rate_bt = h_mins(losing_prob, num_bits, rate_sv)

        # EXT1 (Dodis):
        epsilon_dodis = epsilon_sec/2
        n_dodis = na_set(num_bits-1)+1
        diff = num_bits - n_dodis
        # Adjust rate_bt in case bits need to be dropped due to
        # Dodis input size restriction.
        rate_bt = (num_bits*rate_bt-diff)/(num_bits-diff)
        bits = bits[:n_dodis]
        if na_set(n_dodis-1)+1 != n_dodis:
            raise ValueError("Wrong computation in the first extractor input size.")
        dodis_output_len = dodis_output_size(
            n_dodis, rate_bt, rate_sv, epsilon_dodis, quantum_proof)
        if dodis_output_len < 50:
            raise ValueError('Not enough output for the first extractor. Try '
                             'reducing security parameters or increasing sample size.')

        raw_bytes = bitarray_to_bytes(bits)
        wsr_bytes = bitarray_to_bytes(wsr_generator(n_dodis))

        # EXT2 (Hayashi):
        ext2_params = [0, 0]
        if trusted_backend and not privacy:
            max_hayashi_size = 5*10**8
            epsilon_hayashi_tolerance = epsilon_dodis

            tem = round((1 - rate_sv) * 10**8) / 10**8
            c_max = floor(1 / tem)
            hayashi_inputs = na_set(dodis_output_len)
            if hayashi_inputs > max_hayashi_size:
                raise ValueError('Input size is too large for the second extractor '
                                 'to handled.')

            epsilon_hayashi = 1
            c_pen = 0
            c = 0
            while epsilon_hayashi > epsilon_hayashi_tolerance:
                if c_pen == c_max:
                    raise ValueError('Invalid security parameters for the second extractor.')
                c, epsilon_hayashi = hayashi_parameters(hayashi_inputs, rate_sv, c_max, c_pen)
                c_pen += 1

            ext2_params = [hayashi_inputs, c]

        return CQCExtractorParams(
            ext1_input_num_bits=n_dodis,
            ext1_output_num_bits=dodis_output_len,
            ext1_raw_bytes=raw_bytes,
            ext1_wsr_bytes=wsr_bytes,
            ext2_seed_num_bits=ext2_params[0],
            ext2_wsr_multiplier=ext2_params[1],
            ext2_wsr_generator=wsr_generator)