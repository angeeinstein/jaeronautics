/**
 * The membership form, from both doors: the public signup (/join), which
 * makes the login too, and a membership for somebody signed in without one
 * (/account/create-membership), which uses their login's address.
 *
 * Step by step (docs/frontend-structure.md, 6, Joining): first which kind of member
 * somebody is, which decides what the next steps ask and how they say it;
 * then the name and what that kind is asked (the year group, the university
 * address, the company) with the forum name it makes; then how to reach them;
 * the password (signup only); and last everything once more, what it costs and
 * the texts to accept. Each step is checked before the next; the server checks
 * all of it again and a field it refuses takes the form back to its step.
 *
 * The rules are the server's (forms.py, through aeronautics_members/api/signup.py):
 * what each kind is asked comes from GET /api/v1/forms/options, the texts to
 * accept from GET /api/v1/legal -- each opens over the form, so reading one
 * loses nothing -- and the price from GET /api/v1/signup/price. The answer is
 * where the browser goes next: Stripe's payment page, or My Account.
 */
import {
  Anchor,
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  PasswordInput,
  Radio,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
  VisuallyHidden,
} from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { IconArrowRight, IconCheck, IconMessages } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Fragment, useEffect, useRef, useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { AppLink } from '../../app/AppLink';
import { LegalTextBody } from '../../components/legal/LegalText';
import { Panel } from '../../components/Panel';
import { isYearGroup, YearGroupPicker } from '../../components/YearGroupPicker';
import { ErrorState, LoadingState } from '../../components/States';
import { formatDate } from '../../lib/format';
import { emptyToNull } from '../../lib/forms';
import { messageOf } from '../../lib/notify';
import { type FormOptions, formOptionsQuery } from '../account/shared';
import { forumName } from './join/forumName';
import classes from './join/Join.module.css';
import { wordingFor } from './join/kinds';

type Profile = Omit<Schemas['MembershipIn'], 'terms_accepted' | 'payment_method'>;
type Kind = Schemas['MemberCategoryOut'];
type LegalLink = Schemas['LegalTextLinkOut'];
type Price = Schemas['PriceOut'];

/** The public signup, or a membership for the login of ``email``. */
export type Door = { kind: 'signup' } | { kind: 'profile'; email: string };

type StepId = 'kind' | 'about' | 'contact' | 'password' | 'check';
/** A line of the last step: what, as given, and the step it was given in. */
type Line = [label: string, shown: string, back: StepId];

const STEP_LABELS: Record<StepId, string> = {
  kind: 'Who you are',
  about: 'About you',
  contact: 'Contact',
  password: 'Password',
  check: 'Check and join',
};

/** Where each field the server may refuse is asked. */
const FIELD_STEP: Record<string, StepId> = {
  member_category: 'kind',
  salutation: 'about',
  title: 'about',
  first_name: 'about',
  last_name: 'about',
  year_group: 'about',
  email_work: 'about',
  phone_work: 'about',
  company_name: 'about',
  email_private: 'contact',
  phone_private: 'contact',
  street: 'contact',
  house_number: 'contact',
  postal_code: 'contact',
  city: 'contact',
  country: 'contact',
  password: 'password',
  terms_accepted: 'check',
};

const EMPTY: Profile = {
  salutation: '',
  title: '',
  first_name: '',
  last_name: '',
  street: '',
  house_number: '',
  postal_code: '',
  city: '',
  country: '',
  phone_private: '',
  phone_work: '',
  email_work: '',
  company_name: '',
  member_category: '',
  year_group: '',
};

const AN_ADDRESS = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** How long a chosen card stays on screen before the next step: long enough to see the tick. */
const ADVANCE_AFTER_MS = 450;

function onDomain(address: string, domains: string[]) {
  const domain = address.trim().toLowerCase().split('@')[1] ?? '';
  return domains.some((allowed) => domain === allowed || domain.endsWith(`.${allowed}`));
}

function passwordStrength(password: string): 0 | 1 | 2 | 3 {
  if (!password) return 0;
  if (password.length < 8) return 1;
  const mixed = /[A-Za-z]/.test(password) && /[^A-Za-z]/.test(password);
  return password.length >= 12 && mixed ? 3 : 2;
}

/** One text to accept, over the form. */
function LegalTextDialog({ reading, onClose }: { reading: LegalLink | null; onClose: () => void }) {
  const slug = reading?.slug ?? null;
  const text = useQuery({
    queryKey: ['legal', slug ?? '', null, null] as const,
    queryFn: () => call(api.GET('/api/v1/legal/{slug}', { params: { path: { slug: slug ?? '' } } })),
    enabled: slug !== null,
  });
  return (
    <Modal opened={slug !== null} onClose={onClose} size="xl" title={reading?.name} centered>
      {text.isPending ? (
        <LoadingState />
      ) : text.isError ? (
        <ErrorState error={text.error} onRetry={() => void text.refetch()} />
      ) : (
        <LegalTextBody text={text.data} />
      )}
    </Modal>
  );
}

/** "the Statutes, the Rules of Procedure and the Privacy Policy", each a link that opens it. */
function TextLinks({ texts, onOpen }: { texts: LegalLink[]; onOpen: (text: LegalLink) => void }) {
  return (
    <>
      {texts.map((text, index) => (
        <Fragment key={text.slug}>
          {index === 0 ? 'the ' : index === texts.length - 1 ? ' and the ' : ', the '}
          <Anchor
            href={text.url}
            target="_blank"
            rel="noopener"
            underline="always"
            onClick={(event) => {
              // A new tab when asked for one; else the text over the form.
              if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
              event.preventDefault();
              onOpen(text);
            }}
          >
            {text.name}
          </Anchor>
        </Fragment>
      ))}
    </>
  );
}

function AcceptLabel({ onOpen }: { onOpen: (text: LegalLink) => void }) {
  const legal = useQuery({ queryKey: ['legal'] as const, queryFn: () => call(api.GET('/api/v1/legal')) });
  const texts = legal.data?.texts.filter((text) => text.accepted_at_signup) ?? [];
  if (!texts.length) {
    return (
      <>
        I accept the{' '}
        <Anchor component={AppLink} to="/legal" target="_blank" underline="always">
          legal texts
        </Anchor>
        .
      </>
    );
  }
  return (
    <>
      I accept <TextLinks texts={texts} onOpen={onOpen} />.
    </>
  );
}

function Refusal({ error }: { error: Error }) {
  const exists = error instanceof ApiError && error.code === 'account_exists';
  return (
    <Alert color={exists ? 'amber' : 'red'} variant="light" role="alert">
      <Stack gap="xs">
        <Text size="sm">{messageOf(error)}</Text>
        {exists ? (
          <div>
            <Button component={AppLink} to="/login" size="xs">
              Sign in
            </Button>
          </div>
        ) : null}
      </Stack>
    </Alert>
  );
}

/** Where the steps are: each done one a way back to it. */
function StepBar({ steps, at, onGo }: { steps: StepId[]; at: number; onGo: (index: number) => void }) {
  return (
    <nav aria-label="Steps">
      <Stack gap="xs">
        <ol className={classes.steps}>
          {steps.map((step, index) => {
            const done = index < at;
            return (
              <li key={step}>
                <button
                  type="button"
                  className={classes.stepButton}
                  disabled={!done}
                  data-done={done || undefined}
                  aria-current={index === at ? 'step' : undefined}
                  onClick={() => {
                    onGo(index);
                  }}
                >
                  <span className={classes.stepMark} aria-hidden>
                    {done ? <IconCheck size={15} stroke={3} /> : index + 1}
                  </span>
                  <span className={classes.stepLabel}>
                    {STEP_LABELS[step]}
                    {done ? <VisuallyHidden> (done)</VisuallyHidden> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
        <div className={classes.track} aria-hidden>
          <div className={classes.fill} style={{ width: `${String((at / (steps.length - 1)) * 100)}%` }} />
        </div>
      </Stack>
    </nav>
  );
}

/** What comes after joining, for this person. */
function AfterJoining({ price, twoAddresses }: { price: Price | null; twoAddresses: boolean }) {
  return (
    <ol className={classes.after}>
      <li>
        {price && !price.due_today
          ? 'On the next page, say how you pay. Nothing is charged today.'
          : 'Pay on the next page.'}
      </li>
      <li>
        {twoAddresses
          ? 'Confirm both email addresses: we send a link to each.'
          : 'Confirm your email address: we send you a link.'}
      </li>
      <li>Add a profile picture. Then the forum opens.</li>
    </ol>
  );
}

function PriceBox({ price }: { price: Price | null | undefined }) {
  if (price === undefined) return null;
  if (price === null) {
    return (
      <Text size="sm" c="dimmed">
        The payment page shows what it costs.
      </Text>
    );
  }
  return (
    <section className={classes.price} aria-label="What it costs">
      <Stack gap={2}>
        <Text size="sm" c="dimmed">
          Today
        </Text>
        <span className={classes.amount}>{price.due_today ?? 'Nothing'}</span>
        <Text size="sm" c="dimmed">
          {price.due_today
            ? `For your membership until ${formatDate(price.paid_until)}.`
            : `Free until ${formatDate(price.paid_until)}.`}
        </Text>
      </Stack>
      <Stack gap={2}>
        <Text size="sm" c="dimmed">
          Then every year
        </Text>
        <span className={classes.amount}>{price.annual_fee}</span>
        <Text size="sm" c="dimmed">
          {`Next on ${formatDate(price.renews_on)}. Cancel any time in My Account.`}
        </Text>
      </Stack>
    </section>
  );
}

export function MembershipForm({ door }: { door: Door }) {
  const options = useQuery(formOptionsQuery);
  if (options.isPending) return <LoadingState />;
  if (options.isError) return <ErrorState error={options.error} onRetry={() => void options.refetch()} />;
  return <Form door={door} options={options.data} />;
}

function Form({ door, options }: { door: Door; options: FormOptions }) {
  const steps: StepId[] =
    door.kind === 'signup'
      ? ['kind', 'about', 'contact', 'password', 'check']
      : ['kind', 'about', 'contact', 'check'];
  const kinds = options.member_categories.filter((category) => category.joinable);
  const [at, setAt] = useState(0);
  const [direction, setDirection] = useState<'forward' | 'back'>('forward');
  const [value, setValue] = useState<Profile>(() => ({
    ...EMPTY,
    country: options.countries.some((country) => country.value === 'Austria') ? 'Austria' : '',
  }));
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [workEmailOpened, setWorkEmailOpened] = useState(false);
  const [signInWith, setSignInWith] = useState<'institute' | 'private'>('institute');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [reading, setReading] = useState<LegalLink | null>(null);
  // Counts the refusals, the form's own and the server's: after each, the first refused field gets the focus.
  const [refusals, setRefusals] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const column = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  const advance = useRef<number | undefined>(undefined);
  const reduceMotion = useReducedMotion();
  const price = useQuery({
    queryKey: ['signup', 'price'] as const,
    queryFn: () => call(api.GET('/api/v1/signup/price')),
    staleTime: 5 * 60_000,
  });

  const step = steps[at] ?? 'kind';
  const kind: Kind | undefined = kinds.find((category) => category.value === value.member_category);
  const wording = wordingFor(value.member_category);
  const asksYear = kind !== undefined && kind.year_group !== 'hidden';
  const asksWorkEmail =
    kind !== undefined &&
    (kind.work_email_at_joining || (wording.workEmailOnRequest && (workEmailOpened || !!value.email_work)));
  const asksCompany = kind?.company_name ?? false;
  const yearGroup = (value.year_group ?? '').trim();
  const workEmail = (value.email_work ?? '').trim();
  const whose = kind?.work_email_whose ?? null;
  const studentAddress = (address: string) => onDomain(address, options.student_domains);
  const staffAddress = (address: string) =>
    onDomain(address, options.staff_domains) && !onDomain(address, options.student_domains);
  const institutional = (address: string) => studentAddress(address) || staffAddress(address);
  // Staff may keep their account on their institute address, once given as such.
  const mayUseInstitute = door.kind === 'signup' && kind?.account_email === 'private_or_institute';
  const signsInWithInstitute = mayUseInstitute && signInWith === 'institute' && !!workEmail;
  const accountEmail =
    door.kind === 'signup' ? (signsInWithInstitute ? workEmail : email.trim()) : door.email;
  const shownForumName = forumName(
    value.first_name,
    value.last_name,
    asksYear && isYearGroup(yearGroup) ? yearGroup : null,
  );

  useEffect(
    () => () => {
      window.clearTimeout(advance.current);
    },
    [],
  );

  // A new step: its heading takes the focus, and the steps are back in view when scrolled past.
  useEffect(() => {
    if (!moved.current) return;
    heading.current?.focus({ preventScroll: true });
    const top = column.current?.getBoundingClientRect().top ?? 0;
    const bar = document.querySelector('header')?.getBoundingClientRect().bottom ?? 0;
    if (top < bar)
      column.current?.scrollIntoView({ block: 'start', behavior: reduceMotion ? 'auto' : 'smooth' });
  }, [at, reduceMotion]);

  // After a refusal -- not while a refused field is corrected -- to the first field refused.
  useEffect(() => {
    if (refusals) form.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [refusals]);

  const go = (index: number) => {
    window.clearTimeout(advance.current);
    moved.current = true;
    setDirection(index < at ? 'back' : 'forward');
    setAt(index);
  };

  const clear = (key: string) => {
    if (key in errors) {
      setErrors(Object.fromEntries(Object.entries(errors).filter(([field]) => field !== key)));
    }
  };
  const change = (key: keyof Profile, next: string) => {
    setValue((current) => ({ ...current, [key]: next }));
    clear(key);
  };
  const text = (key: keyof Profile) => ({
    value: value[key] ?? '',
    error: errors[key] ?? null,
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
      change(key, event.currentTarget.value);
    },
  });

  /** What this step still lacks, as the server would say it. */
  const lacking = (which: StepId): Record<string, string> => {
    const found: Record<string, string> = {};
    const blank = (text: string | null | undefined) => !(text ?? '').trim();
    if (which === 'kind' && !kind) found.member_category = 'Choose one to go on.';
    if (which === 'about') {
      if (!value.salutation) found.salutation = 'Please choose one.';
      if (blank(value.first_name)) found.first_name = 'Please enter your first name.';
      if (blank(value.last_name)) found.last_name = 'Please enter your last name.';
      if (asksYear) {
        if (!yearGroup && kind.year_group === 'required')
          found.year_group = 'Please choose your programme and the year you started.';
        else if (yearGroup && !isYearGroup(yearGroup))
          found.year_group = 'Three letters and two digits, like LAV25.';
      }
      if (asksWorkEmail) {
        if (!workEmail) {
          if (kind.work_email_at_joining)
            found.email_work =
              whose === 'staff'
                ? 'Please enter your institute address.'
                : 'Please enter your university address.';
        } else if (!AN_ADDRESS.test(workEmail)) found.email_work = 'Please check this address.';
        else if (whose === 'student' && !studentAddress(workEmail))
          found.email_work = `Please use your student address: @${options.student_domains[0] ?? 'university'}.`;
        else if (whose === 'staff' && !staffAddress(workEmail))
          found.email_work = `Please use your institute address: @${options.staff_domains[0] ?? 'institute'}.`;
      }
      if (asksCompany && blank(value.company_name))
        found.company_name = 'Please enter the company you join for.';
    }
    if (which === 'contact') {
      if (door.kind === 'signup' && !signsInWithInstitute) {
        if (!AN_ADDRESS.test(email.trim())) found.email_private = 'Please enter your email address.';
        else if (kind?.account_email !== 'any' && institutional(email))
          found.email_private = mayUseInstitute
            ? 'To sign in with your institute address, choose it above; otherwise use a private one.'
            : 'Please use a private address: a university one stops working when you leave.';
      }
      if (blank(value.phone_private)) found.phone_private = 'Please enter a phone number.';
      if (blank(value.street)) found.street = 'Please enter your street.';
      if (blank(value.house_number)) found.house_number = 'Required.';
      if (blank(value.postal_code)) found.postal_code = 'Required.';
      if (blank(value.city)) found.city = 'Please enter your city.';
      if (!value.country) found.country = 'Please choose your country.';
    }
    if (which === 'password' && password.length < 8) found.password = 'At least 8 characters.';
    if (which === 'check' && !accepted) found.terms_accepted = 'Please accept the texts to join.';
    return found;
  };

  const send = useMutation({
    mutationFn: () => {
      const membership = {
        ...value,
        title: emptyToNull(value.title),
        phone_work: wording.workPhone ? emptyToNull(value.phone_work) : null,
        email_work: asksWorkEmail ? emptyToNull(value.email_work) : null,
        company_name: asksCompany ? emptyToNull(value.company_name) : null,
        year_group: asksYear ? emptyToNull(yearGroup.toUpperCase()) : null,
        terms_accepted: accepted,
      };
      return door.kind === 'signup'
        ? call(api.POST('/api/v1/signup', { body: { ...membership, email_private: accountEmail, password } }))
        : call(api.POST('/api/v1/account/membership', { body: membership }));
    },
    onMutate: () => {
      setErrors({});
    },
    onSuccess: ({ go_to }) => {
      window.location.assign(go_to);
    },
    onError: (error) => {
      if (!(error instanceof ApiError)) return;
      setErrors(error.fields);
      // Back to the first step the server refused something on.
      const first = steps.findIndex((id) =>
        Object.keys(error.fields).some((field) => FIELD_STEP[field] === id),
      );
      if (first !== -1 && first !== at) go(first);
      setRefusals((count) => count + 1);
    },
  });

  const next = () => {
    const found = lacking(step);
    if (Object.keys(found).length) {
      setErrors(found);
      setRefusals((count) => count + 1);
      return;
    }
    if (step === 'check') send.mutate();
    else go(at + 1);
  };

  const pickKind = (chosen: string) => {
    setValue((current) => ({ ...current, member_category: chosen }));
    clear('member_category');
    window.clearTimeout(advance.current);
    advance.current = window.setTimeout(() => {
      go(1);
    }, ADVANCE_AFTER_MS);
  };

  // A refusal of the form as a whole, not of one of its fields.
  const refusedAsAWhole =
    send.error &&
    !(send.error instanceof ApiError && Object.keys(send.error.fields).some((key) => key in FIELD_STEP));
  const twoAddresses = asksWorkEmail && !!workEmail && workEmail.toLowerCase() !== accountEmail.toLowerCase();
  const strength = passwordStrength(password);
  const address = [
    [value.street, value.house_number].filter(Boolean).join(' '),
    [value.postal_code, value.city].filter(Boolean).join(' '),
    value.country,
  ]
    .filter(Boolean)
    .join(', ');
  const summary: Line[] = [
    ['You are', [kind?.label, asksYear ? yearGroup : ''].filter(Boolean).join(' · '), 'kind'],
    [
      'Name',
      [value.salutation === 'Diverse' ? '' : value.salutation, value.title, value.first_name, value.last_name]
        .filter(Boolean)
        .join(' '),
      'about',
    ],
    ...(asksCompany ? [['Company', value.company_name ?? '', 'about'] satisfies Line] : []),
    ...(asksWorkEmail && workEmail ? [[wording.workEmail, workEmail, 'about'] satisfies Line] : []),
    [kind?.account_email === 'private' ? 'Private email' : 'Sign-in email', accountEmail, 'contact'],
    ['Phone', value.phone_private, 'contact'],
    ['Address', address, 'contact'],
    ['Forum name', shownForumName, 'about'],
  ];

  return (
    <div className={classes.layout}>
      <div className={classes.main} ref={column}>
        <StepBar steps={steps} at={at} onGo={go} />
        <form
          ref={form}
          noValidate
          className={classes.card}
          aria-label="Joining"
          onSubmit={(event) => {
            event.preventDefault();
            next();
          }}
        >
          <div key={step} className={classes.step} data-direction={direction}>
            {step === 'kind' ? (
              <Stack gap="lg">
                <Stack gap={4}>
                  <h2 ref={heading} tabIndex={-1} className={classes.heading}>
                    Which describes you?
                  </h2>
                  <Text c="dimmed">What we ask next depends on it.</Text>
                </Stack>
                <Radio.Group
                  value={value.member_category || null}
                  onChange={pickKind}
                  error={errors.member_category ?? null}
                  aria-label="Which describes you?"
                >
                  <div className={classes.kinds}>
                    {kinds.map((choice) => {
                      const { icon: Icon, line } = wordingFor(choice.value);
                      const checked = choice.value === value.member_category;
                      return (
                        <Radio.Card
                          key={choice.value}
                          value={choice.value}
                          radius={0}
                          className={classes.kind}
                          data-checked={checked || undefined}
                          onClick={() => {
                            // Chosen again: on, as the choice already stands.
                            if (checked) pickKind(choice.value);
                          }}
                        >
                          <span className={classes.kindIcon} aria-hidden>
                            <Icon size={24} stroke={1.8} />
                          </span>
                          <Stack gap={2}>
                            <span className={classes.kindName}>{choice.label}</span>
                            <Text size="sm" c="dimmed">
                              {line ?? choice.description}
                            </Text>
                          </Stack>
                          {checked ? (
                            <span className={classes.tick} aria-hidden>
                              <IconCheck size={15} stroke={3} />
                            </span>
                          ) : null}
                        </Radio.Card>
                      );
                    })}
                  </div>
                </Radio.Group>
              </Stack>
            ) : null}

            {step === 'about' ? (
              <Stack gap="lg">
                <Stack gap={4}>
                  <h2 ref={heading} tabIndex={-1} className={classes.heading}>
                    About you
                  </h2>
                  <Text c="dimmed">{wording.about}</Text>
                </Stack>
                <Group align="flex-start" gap="md" wrap="wrap">
                  <Radio.Group
                    label="Salutation"
                    required
                    value={value.salutation || null}
                    error={errors.salutation ?? null}
                    onChange={(next) => {
                      change('salutation', next);
                    }}
                  >
                    <Group gap="xs" mt={6}>
                      {options.salutations.map((salutation) => (
                        <Radio key={salutation.value} value={salutation.value} label={salutation.label} />
                      ))}
                    </Group>
                  </Radio.Group>
                  <TextInput
                    label="Title"
                    description="If you use one"
                    autoComplete="honorific-prefix"
                    maxLength={50}
                    w={{ base: '100%', xs: 200 }}
                    {...text('title')}
                  />
                </Group>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  <TextInput
                    label="First name"
                    required
                    autoComplete="given-name"
                    maxLength={100}
                    {...text('first_name')}
                  />
                  <TextInput
                    label="Last name"
                    required
                    autoComplete="family-name"
                    maxLength={100}
                    {...text('last_name')}
                  />
                </SimpleGrid>

                <section className={`${classes.block} ${classes.reveal}`} aria-label={wording.block}>
                  <Title order={3} size="h5">
                    {wording.block}
                  </Title>
                  {asksCompany ? (
                    <TextInput
                      label="Company"
                      required
                      autoComplete="organization"
                      maxLength={255}
                      className={classes.reveal}
                      {...text('company_name')}
                    />
                  ) : null}
                  {asksYear ? (
                    <div className={classes.reveal}>
                      <YearGroupPicker
                        value={yearGroup}
                        programmes={options.programmes}
                        required={kind.year_group === 'required'}
                        error={errors.year_group ?? null}
                        onChange={(next) => {
                          change('year_group', next);
                        }}
                      />
                    </div>
                  ) : null}
                  {asksWorkEmail ? (
                    <Stack gap={6} className={classes.reveal}>
                      <TextInput
                        label={wording.workEmail}
                        description={
                          whose === 'student'
                            ? `Your @${options.student_domains[0] ?? 'university'} address: it shows you study here.`
                            : whose === 'staff'
                              ? `Your @${options.staff_domains[0] ?? 'institute'} address: it shows you work here.`
                              : wording.workEmailOnRequest
                                ? 'It gives you back your old forum account, if you had one.'
                                : undefined
                        }
                        required={kind.work_email_at_joining}
                        type="email"
                        maxLength={255}
                        {...text('email_work')}
                      />
                      {((whose === 'student' && studentAddress(workEmail)) ||
                        (whose === 'staff' && staffAddress(workEmail))) &&
                      !errors.email_work ? (
                        <span className={`${classes.good} ${classes.reveal}`}>
                          <IconCheck size={14} stroke={3} aria-hidden />
                          {whose === 'student' ? 'A student address.' : 'An institute address.'} We send it a
                          link to confirm.
                        </span>
                      ) : null}
                    </Stack>
                  ) : kind && wording.workEmailOnRequest ? (
                    <div>
                      <Anchor
                        component="button"
                        type="button"
                        size="sm"
                        underline="always"
                        onClick={() => {
                          setWorkEmailOpened(true);
                        }}
                      >
                        My university address still works
                      </Anchor>
                    </div>
                  ) : null}
                  {wording.workPhone ? (
                    <TextInput
                      label={wording.workPhone}
                      type="tel"
                      maxLength={50}
                      w={{ base: '100%', xs: 320 }}
                      {...text('phone_work')}
                    />
                  ) : null}
                </section>

                <div className={classes.forumName} aria-live="polite">
                  <span className={classes.forumIcon} aria-hidden>
                    <IconMessages size={20} stroke={1.8} />
                  </span>
                  <Stack gap={0} style={{ minWidth: 0 }}>
                    <Text size="xs" c="dimmed">
                      Your forum name
                    </Text>
                    <span className={classes.forumValue} data-empty={shownForumName ? undefined : true}>
                      {shownForumName || 'Appears as you type'}
                    </span>
                  </Stack>
                </div>
              </Stack>
            ) : null}

            {step === 'contact' ? (
              <Stack gap="lg">
                <Stack gap={4}>
                  <h2 ref={heading} tabIndex={-1} className={classes.heading}>
                    How we reach you
                  </h2>
                  <Text c="dimmed">For the association’s news, and your membership.</Text>
                </Stack>
                {mayUseInstitute && workEmail ? (
                  <Radio.Group
                    label="Sign in with"
                    value={signInWith}
                    onChange={(next) => {
                      setSignInWith(next === 'private' ? 'private' : 'institute');
                      clear('email_private');
                    }}
                  >
                    <Stack gap="xs" mt={8}>
                      <Radio value="institute" label={`My institute address, ${workEmail}`} />
                      <Radio value="private" label="A private address" />
                    </Stack>
                  </Radio.Group>
                ) : null}
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                  {door.kind === 'signup' ? (
                    signsInWithInstitute ? null : (
                      <TextInput
                        className={classes.reveal}
                        label={kind?.account_email === 'any' ? 'Email' : 'Private email'}
                        description={
                          mayUseInstitute
                            ? 'You sign in with it. It keeps working if you leave the institute.'
                            : wording.privateEmail
                        }
                        required
                        type="email"
                        autoComplete="email"
                        maxLength={255}
                        value={email}
                        error={errors.email_private ?? null}
                        onChange={(event) => {
                          setEmail(event.currentTarget.value);
                          clear('email_private');
                        }}
                      />
                    )
                  ) : (
                    <TextInput
                      label="Sign-in email"
                      description="Your login’s address."
                      value={door.email}
                      readOnly
                      error={errors.email_private ?? null}
                    />
                  )}
                  <TextInput
                    label="Phone"
                    required
                    type="tel"
                    autoComplete="tel"
                    maxLength={50}
                    {...text('phone_private')}
                  />
                </SimpleGrid>
                <Stack gap="md" component="fieldset" style={{ border: 0, margin: 0, padding: 0 }}>
                  <Title order={3} size="h5" component="legend" mb="sm" p={0}>
                    Address
                  </Title>
                  <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="md">
                    <TextInput
                      label="Street"
                      required
                      autoComplete="address-line1"
                      maxLength={255}
                      {...text('street')}
                    />
                    <TextInput label="House number" required maxLength={20} {...text('house_number')} />
                    <TextInput
                      label="Postal code"
                      required
                      autoComplete="postal-code"
                      maxLength={20}
                      {...text('postal_code')}
                    />
                    <TextInput
                      label="City"
                      required
                      autoComplete="address-level2"
                      maxLength={100}
                      {...text('city')}
                    />
                  </SimpleGrid>
                  <Select
                    label="Country"
                    required
                    data={options.countries}
                    searchable
                    w={{ base: '100%', xs: 320 }}
                    value={value.country || null}
                    error={errors.country ?? null}
                    onChange={(next) => {
                      change('country', next ?? '');
                    }}
                  />
                </Stack>
              </Stack>
            ) : null}

            {step === 'password' ? (
              <Stack gap="lg">
                <Stack gap={4}>
                  <h2 ref={heading} tabIndex={-1} className={classes.heading}>
                    Your password
                  </h2>
                  <Text c="dimmed">{`You sign in with ${email.trim() || 'your email'} and this.`}</Text>
                </Stack>
                <Stack gap={8} maw={440}>
                  <PasswordInput
                    label="Password"
                    required
                    autoComplete="new-password"
                    maxLength={128}
                    value={password}
                    error={errors.password ?? null}
                    onChange={(event) => {
                      setPassword(event.currentTarget.value);
                      clear('password');
                    }}
                  />
                  <div className={classes.meter} data-strength={strength} aria-hidden>
                    <span />
                    <span />
                    <span />
                  </div>
                  <Text size="xs" c="dimmed" aria-live="polite">
                    {password.length < 8
                      ? password
                        ? `${String(8 - password.length)} more to go.`
                        : 'At least 8 characters.'
                      : strength === 3
                        ? 'Strong.'
                        : 'Good. Longer, with digits or symbols, is stronger.'}
                  </Text>
                </Stack>
              </Stack>
            ) : null}

            {step === 'check' ? (
              <Stack gap="lg">
                <Stack gap={4}>
                  <h2 ref={heading} tabIndex={-1} className={classes.heading}>
                    Check and join
                  </h2>
                  <Text c="dimmed">Anything to change? Each line takes you back to it.</Text>
                </Stack>
                <dl className={classes.summary}>
                  {summary.map(([label, shown, back]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{shown || '–'}</dd>
                      <Button
                        variant="subtle"
                        size="compact-sm"
                        aria-label={`Change ${label.toLowerCase()}`}
                        onClick={() => {
                          go(steps.indexOf(back));
                        }}
                      >
                        Change
                      </Button>
                    </div>
                  ))}
                </dl>
                <PriceBox price={price.isError ? null : price.data?.price} />
                <Stack gap="sm" className={classes.inlineAfter}>
                  <Title order={3} size="h5">
                    After joining
                  </Title>
                  <AfterJoining price={price.data?.price ?? null} twoAddresses={twoAddresses} />
                </Stack>
                <Checkbox
                  label={<AcceptLabel onOpen={setReading} />}
                  checked={accepted}
                  error={errors.terms_accepted ?? null}
                  onChange={(event) => {
                    setAccepted(event.currentTarget.checked);
                    clear('terms_accepted');
                  }}
                />
                {refusedAsAWhole ? <Refusal error={send.error} /> : null}
              </Stack>
            ) : null}
          </div>

          <div className={classes.actions}>
            {at > 0 ? (
              <Button
                variant="default"
                onClick={() => {
                  go(at - 1);
                }}
              >
                Back
              </Button>
            ) : null}
            <Button
              type="submit"
              className={classes.push}
              rightSection={step === 'check' ? null : <IconArrowRight size={18} />}
              loading={send.isPending || send.isSuccess}
            >
              {step === 'check'
                ? `${door.kind === 'signup' ? 'Join' : 'Start membership'} and continue to payment`
                : 'Continue'}
            </Button>
          </div>
        </form>
      </div>
      <aside className={classes.aside} aria-label="After joining">
        <Panel title="After joining">
          <AfterJoining price={price.data?.price ?? null} twoAddresses={twoAddresses} />
        </Panel>
      </aside>
      <LegalTextDialog
        reading={reading}
        onClose={() => {
          setReading(null);
        }}
      />
    </div>
  );
}
